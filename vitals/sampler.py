"""Metric sampling: turns psutil/GPU readings into snapshots + rolling history."""

from __future__ import annotations

import collections
import os
import pwd
import threading
import time
from dataclasses import dataclass, field

import psutil

from . import gpu as gpu_mod

HISTORY_LEN = 120

CLOCK_TICKS = os.sysconf("SC_CLK_TCK")
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")

# Temperatures move slowly and psutil's sensor scan is by far the most
# expensive whole-system call, so it runs on its own slower cadence.
TEMP_EVERY = 5

# Process enumeration dominates the tick cost, so when its table is hidden we
# only refresh it occasionally to keep counters and GPU client names current.
BACKGROUND_PROC_EVERY = 4

_PROC_STATES = {
    "R": "running", "S": "sleeping", "D": "disk-sleep", "Z": "zombie",
    "T": "stopped", "t": "tracing", "X": "dead", "I": "idle",
    "W": "waking", "P": "parked",
}

# Virtual/uninteresting interfaces are hidden from the network page by default.
_SKIP_NIC_PREFIXES = ("lo", "veth", "br-", "docker", "virbr", "tun", "tap")


def _parse_stat(raw: bytes) -> tuple[str, int, str, int, int, int]:
    """Extract (comm, ppid, state, cpu_ticks, num_threads, rss_pages).

    The comm field is wrapped in parentheses and may itself contain spaces and
    parentheses, so parsing starts after its final ')'.
    """
    open_paren = raw.index(b"(")
    close_paren = raw.rindex(b")")
    comm = raw[open_paren + 1:close_paren].decode("utf-8", "replace")
    fields = raw[close_paren + 2:].split()
    # Offsets are relative to field 3 (state) of proc(5).
    state = fields[0].decode("ascii", "replace")
    ppid = int(fields[1])
    utime = int(fields[11])
    stime = int(fields[12])
    threads = int(fields[17])
    rss_pages = int(fields[21])
    return comm, ppid, state, utime + stime, threads, rss_pages


# Helper processes that belong to this window, not to stacks Vitals launched.
_SELF_CHILD_PREFIXES = ("WebKit", "nvidia-smi")


def _own_cost_pids(root: int) -> list[int]:
    """This PID plus WebKit / nvidia-smi descendants — not spawned local apps."""
    found = [root]
    stack = [root]
    seen = {root}
    while stack:
        pid = stack.pop()
        try:
            tasks = os.listdir(f"/proc/{pid}/task")
        except OSError:
            continue
        for tid in tasks:
            try:
                with open(f"/proc/{pid}/task/{tid}/children", encoding="ascii") as handle:
                    text = handle.read()
            except OSError:
                continue
            for token in text.split():
                child = int(token)
                if child in seen:
                    continue
                seen.add(child)
                try:
                    with open(f"/proc/{child}/stat", "rb") as handle:
                        comm = _parse_stat(handle.read())[0]
                except (OSError, ValueError, IndexError):
                    continue
                if comm.startswith(_SELF_CHILD_PREFIXES):
                    found.append(child)
                    stack.append(child)
    return found


class Series:
    """Fixed-length ring buffer of floats, oldest first.

    ``filled`` tracks how many real samples exist so charts can grow in from
    the right edge instead of showing a false flat line at zero.
    """

    __slots__ = ("data", "maxlen", "filled", "_peak")

    def __init__(self, maxlen: int = HISTORY_LEN) -> None:
        self.maxlen = maxlen
        self.filled = 0
        self._peak = 0.0
        self.data: collections.deque[float] = collections.deque(
            [0.0] * maxlen, maxlen=maxlen
        )

    def push(self, value: float) -> None:
        value = float(value)
        dropped = self.data[0] if self.filled >= self.maxlen else None
        self.data.append(value)
        if self.filled < self.maxlen:
            self.filled += 1
            if value > self._peak:
                self._peak = value
            return
        if value >= self._peak:
            self._peak = value
        elif dropped is not None and dropped >= self._peak:
            self._peak = max(self.data) if self.data else 0.0

    @property
    def latest(self) -> float:
        return self.data[-1]

    def peak(self) -> float:
        return self._peak if self.filled else 0.0

    def mean(self) -> float:
        real = self.real_values()
        return sum(real) / len(real) if real else 0.0

    def values(self) -> list[float]:
        return list(self.data)

    def real_values(self) -> list[float]:
        """Only the samples actually recorded so far."""
        if self.filled >= self.maxlen:
            return list(self.data)
        return list(self.data)[self.maxlen - self.filled:] if self.filled else []


@dataclass
class ProcRow:
    pid: int
    name: str
    user: str
    cpu: float
    mem_percent: float
    rss: float
    threads: int
    status: str
    vram: float = 0.0
    cmdline: str = ""


@dataclass
class Snapshot:
    timestamp: float = 0.0
    cpu_total: float = 0.0
    cpu_per_core: list[float] = field(default_factory=list)
    cpu_freqs: list[float] = field(default_factory=list)
    cpu_freq_avg: float = 0.0
    cpu_temp: float = 0.0
    load_avg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    ctx_switches: float = 0.0

    mem_total: float = 0.0
    mem_used: float = 0.0
    mem_available: float = 0.0
    mem_cached: float = 0.0
    mem_buffers: float = 0.0
    mem_percent: float = 0.0

    swap_total: float = 0.0
    swap_used: float = 0.0
    swap_percent: float = 0.0
    swap_in_rate: float = 0.0
    swap_out_rate: float = 0.0

    net_rx_rate: float = 0.0
    net_tx_rate: float = 0.0
    net_rx_total: float = 0.0
    net_tx_total: float = 0.0
    per_nic: dict[str, dict[str, float]] = field(default_factory=dict)

    disk_read_rate: float = 0.0
    disk_write_rate: float = 0.0

    gpu: gpu_mod.GpuSample = field(default_factory=gpu_mod.GpuSample)

    processes: list[ProcRow] = field(default_factory=list)
    proc_count: int = 0      # every process on the system
    proc_shown: int = 0      # rows actually listed
    thread_count: int = 0
    uptime: float = 0.0
    sample_ms: float = 0.0
    self_cpu: float = 0.0
    self_rss: float = 0.0


class Sampler:
    def __init__(self) -> None:
        self.core_count = psutil.cpu_count(logical=True) or 1
        self.physical_cores = psutil.cpu_count(logical=False) or self.core_count
        self.gpu_backend = gpu_mod.create_backend()

        self.hist_cpu = Series()
        self.hist_cores = [Series() for _ in range(self.core_count)]
        self.hist_mem = Series()
        self.hist_swap = Series()
        self.hist_rx = Series()
        self.hist_tx = Series()
        self.hist_gpu = Series()
        self.hist_vram = Series()
        self.hist_gpu_temp = Series()
        self.hist_disk_r = Series()
        self.hist_disk_w = Series()
        self.hist_nic: dict[str, tuple[Series, Series]] = {}

        self._last_time = time.monotonic()
        self._last_net = psutil.net_io_counters()
        self._last_net_nic = psutil.net_io_counters(pernic=True)
        self._last_disk = psutil.disk_io_counters()
        self._last_swap = psutil.swap_memory()
        self._last_ctx = psutil.cpu_stats().ctx_switches
        self._temp_tick = 0
        self._temp_cache: float | None = None
        # name/username/kind never change for a live PID, so resolve them once.
        self._proc_static: dict[int, tuple[str, str, bool]] = {}
        self._proc_ticks: dict[int, int] = {}
        self._last_proc_scan = 0.0
        self._boot_time = psutil.boot_time()
        self.started_at = time.monotonic()
        self._self_ticks: dict[int, int] = {}
        self._self_pid = os.getpid()
        self._last_proc_count = 0
        self._last_thread_count = 0
        self.last_processes: list[ProcRow] = []
        # Kernel threads are numerous, always idle and never interesting here,
        # so they are excluded unless explicitly requested.
        self.include_kernel = False
        self.snapshot = Snapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._interval_ms = 1000
        self._tick_count = 0
        self._want_processes = False

        psutil.cpu_percent(percpu=True)  # prime the deltas
        psutil.cpu_percent()

    def start_loop(self, interval_ms: int) -> None:
        """Run sampling on a worker so a stuck /proc or hwmon read cannot freeze GTK."""
        self._interval_ms = max(200, interval_ms)
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(
            target=self._loop, name="vitals-sample", daemon=True,
        )
        self._thread.start()

    def stop_loop(self) -> None:
        self._stop.set()
        self._paused.clear()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None

    def set_interval_ms(self, interval_ms: int) -> None:
        self._interval_ms = max(200, interval_ms)

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def set_want_processes(self, want: bool) -> None:
        self._want_processes = want

    def latest(self) -> Snapshot:
        with self._lock:
            return self.snapshot

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.2)
                continue
            started = time.perf_counter()
            self._tick_count += 1
            want = (
                self._want_processes
                or self._tick_count == 1
                or self._tick_count % BACKGROUND_PROC_EVERY == 0
            )
            try:
                snap = self.sample(want_processes=want)
                snap.sample_ms = (time.perf_counter() - started) * 1000
                with self._lock:
                    self.snapshot = snap
            except Exception:
                pass
            elapsed = time.perf_counter() - started
            wait = max(0.05, self._interval_ms / 1000.0 - elapsed)
            self._stop.wait(wait)

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def is_physical_nic(name: str) -> bool:
        return not name.startswith(_SKIP_NIC_PREFIXES)

    def _cpu_temperature(self) -> float:
        self._temp_tick = (self._temp_tick + 1) % TEMP_EVERY
        if self._temp_tick != 1 and self._temp_cache is not None:
            return self._temp_cache
        value = self._read_cpu_temperature()
        self._temp_cache = value
        return value

    def _read_cpu_temperature(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
        except (AttributeError, OSError):
            return 0.0
        for key in ("coretemp", "k10temp", "zenpower", "acpitz", "cpu_thermal"):
            entries = temps.get(key)
            if not entries:
                continue
            for entry in entries:
                if entry.label in ("Package id 0", "Tctl", "Tdie", ""):
                    return entry.current or 0.0
            return entries[0].current or 0.0
        return 0.0

    # -- main tick -------------------------------------------------------

    def sample(self, want_processes: bool = True) -> Snapshot:
        now = time.monotonic()
        dt = max(1e-3, now - self._last_time)
        self._last_time = now
        s = Snapshot(timestamp=now)

        # CPU
        s.cpu_per_core = psutil.cpu_percent(percpu=True)
        s.cpu_total = sum(s.cpu_per_core) / len(s.cpu_per_core) if s.cpu_per_core else 0.0
        self.hist_cpu.push(s.cpu_total)
        for i, val in enumerate(s.cpu_per_core):
            if i < len(self.hist_cores):
                self.hist_cores[i].push(val)
        try:
            freqs = psutil.cpu_freq(percpu=True)
            s.cpu_freqs = [f.current for f in freqs]
        except (OSError, AttributeError):
            s.cpu_freqs = []
        if not s.cpu_freqs:
            try:
                cur = psutil.cpu_freq()
                s.cpu_freqs = [cur.current] if cur else []
            except (OSError, AttributeError):
                s.cpu_freqs = []
        s.cpu_freq_avg = sum(s.cpu_freqs) / len(s.cpu_freqs) if s.cpu_freqs else 0.0
        s.cpu_temp = self._cpu_temperature()
        try:
            s.load_avg = psutil.getloadavg()
        except (OSError, AttributeError):
            s.load_avg = (0.0, 0.0, 0.0)
        ctx = psutil.cpu_stats().ctx_switches
        s.ctx_switches = max(0.0, (ctx - self._last_ctx) / dt)
        self._last_ctx = ctx

        # Memory
        vm = psutil.virtual_memory()
        s.mem_total = vm.total
        s.mem_available = vm.available
        s.mem_used = vm.total - vm.available
        s.mem_cached = getattr(vm, "cached", 0.0)
        s.mem_buffers = getattr(vm, "buffers", 0.0)
        s.mem_percent = s.mem_used / vm.total * 100 if vm.total else 0.0
        self.hist_mem.push(s.mem_percent)

        # Swap
        sw = psutil.swap_memory()
        s.swap_total = sw.total
        s.swap_used = sw.used
        s.swap_percent = sw.percent
        s.swap_in_rate = max(0.0, (sw.sin - self._last_swap.sin) / dt)
        s.swap_out_rate = max(0.0, (sw.sout - self._last_swap.sout) / dt)
        self._last_swap = sw
        self.hist_swap.push(s.swap_percent)

        # Network
        net = psutil.net_io_counters()
        s.net_rx_rate = max(0.0, (net.bytes_recv - self._last_net.bytes_recv) / dt)
        s.net_tx_rate = max(0.0, (net.bytes_sent - self._last_net.bytes_sent) / dt)
        s.net_rx_total = net.bytes_recv
        s.net_tx_total = net.bytes_sent
        self._last_net = net
        self.hist_rx.push(s.net_rx_rate)
        self.hist_tx.push(s.net_tx_rate)

        per_nic = psutil.net_io_counters(pernic=True)
        stats = psutil.net_if_stats()
        for name, counters in per_nic.items():
            prev = self._last_net_nic.get(name)
            rx = max(0.0, (counters.bytes_recv - prev.bytes_recv) / dt) if prev else 0.0
            tx = max(0.0, (counters.bytes_sent - prev.bytes_sent) / dt) if prev else 0.0
            nic_stat = stats.get(name)
            s.per_nic[name] = {
                "rx": rx, "tx": tx,
                "rx_total": counters.bytes_recv, "tx_total": counters.bytes_sent,
                "up": 1.0 if (nic_stat and nic_stat.isup) else 0.0,
                "speed": float(nic_stat.speed) if nic_stat else 0.0,
            }
            # Docker/veth churn used to grow unbounded history; only keep a
            # ring buffer for physical NICs, or ones already being shown.
            if self.is_physical_nic(name) or name in self.hist_nic:
                if name not in self.hist_nic:
                    self.hist_nic[name] = (Series(), Series())
                self.hist_nic[name][0].push(rx)
                self.hist_nic[name][1].push(tx)
        for stale in [name for name in self.hist_nic if name not in per_nic]:
            self.hist_nic.pop(stale, None)
        self._last_net_nic = per_nic

        # Disk
        disk = psutil.disk_io_counters()
        if disk and self._last_disk:
            s.disk_read_rate = max(0.0, (disk.read_bytes - self._last_disk.read_bytes) / dt)
            s.disk_write_rate = max(0.0, (disk.write_bytes - self._last_disk.write_bytes) / dt)
        self._last_disk = disk
        self.hist_disk_r.push(s.disk_read_rate)
        self.hist_disk_w.push(s.disk_write_rate)

        # GPU
        s.gpu = self.gpu_backend.read()
        self.hist_gpu.push(s.gpu.util)
        self.hist_vram.push(s.gpu.mem_percent)
        self.hist_gpu_temp.push(s.gpu.temperature)

        s.uptime = time.time() - self._boot_time
        s.self_cpu, s.self_rss = self._sample_self(dt)
        if want_processes:
            s.processes, s.proc_count, s.thread_count = self._sample_processes(
                s.gpu, s.mem_total or 1,
            )
            s.proc_shown = len(s.processes)
            self.last_processes = s.processes
            self._last_proc_count = s.proc_count
            self._last_thread_count = s.thread_count
        else:
            # Keep the last known figures so the readouts do not flicker.
            s.processes = self.last_processes
            s.proc_count = self._last_proc_count
            s.proc_shown = len(s.processes)
            s.thread_count = self._last_thread_count

        return s

    def track_nic(self, name: str) -> tuple[Series, Series]:
        """Start a history ring for a NIC the UI wants to show."""
        series = self.hist_nic.get(name)
        if series is None:
            series = (Series(), Series())
            self.hist_nic[name] = series
        return series

    def _static_info(self, pid: int, comm: str, ppid: int) -> tuple[str, str, bool]:
        """Return (name, user, is_kernel_thread), cached for the PID's lifetime.

        ``comm`` from /proc/<pid>/stat is capped at 15 characters, so the
        command line supplies a fuller name where one exists.
        """
        info = self._proc_static.get(pid)
        if info is not None:
            return info

        name = comm
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                argv0 = handle.read().split(b"\0", 1)[0]
            if argv0:
                # Some programs (Chrome) flatten their whole command line into
                # argv[0], so keep only the leading path token.
                first = argv0.decode("utf-8", "replace").split(" ", 1)[0]
                candidate = os.path.basename(first)[:48]
                if candidate and (candidate.startswith(comm)
                                  or comm.startswith(candidate)):
                    name = candidate
        except OSError:
            pass

        try:
            uid = os.stat(f"/proc/{pid}").st_uid
            user = pwd.getpwuid(uid).pw_name[:16]
        except (OSError, KeyError):
            user = ""

        # PID 2 is kthreadd; it and its children are the kernel threads.
        info = (name, user, pid == 2 or ppid == 2)
        self._proc_static[pid] = info
        return info

    def name_for(self, pid: int) -> str:
        """Resolve a single process name, used for the small GPU client list."""
        info = self._proc_static.get(pid)
        if info is not None:
            return info[0]
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                raw = handle.read()
            comm, ppid, _ = _parse_stat(raw)[:3]
        except (OSError, ValueError):
            return "unknown"
        return self._static_info(pid, comm, ppid)[0]

    def _sample_self(self, dt: float) -> tuple[float, float]:
        """CPU % and RSS of this process plus WebKit / nvidia-smi children."""
        pids = _own_cost_pids(self._self_pid)
        ticks: dict[int, int] = {}
        cpu = 0.0
        rss = 0.0
        prev = self._self_ticks
        for pid in pids:
            try:
                with open(f"/proc/{pid}/stat", "rb") as handle:
                    _comm, _ppid, _state, used_ticks, _threads, rss_pages = _parse_stat(
                        handle.read()
                    )
            except (OSError, ValueError, IndexError):
                continue
            ticks[pid] = used_ticks
            rss += rss_pages * PAGE_SIZE
            previous = prev.get(pid)
            if previous is not None and dt > 0:
                cpu += (used_ticks - previous) / CLOCK_TICKS / dt * 100
        self._self_ticks = ticks
        return max(0.0, cpu), rss

    def _sample_processes(
        self, gpu_sample: gpu_mod.GpuSample, mem_total: float,
    ) -> tuple[list[ProcRow], int, int]:
        """Read every process from /proc with a single file read each."""
        rows: list[ProcRow] = []
        total_threads = 0
        total = 0
        mem_total = mem_total or 1
        vram_map = gpu_sample.processes
        include_kernel = self.include_kernel

        now = time.monotonic()
        elapsed = now - self._last_proc_scan if self._last_proc_scan else 0.0
        self._last_proc_scan = now
        ticks = self._proc_ticks
        fresh_ticks: dict[int, int] = {}

        with os.scandir("/proc") as entries:
            for entry in entries:
                pid_str = entry.name
                if not pid_str.isdigit():
                    continue
                pid = int(pid_str)
                total += 1
                try:
                    with open(f"/proc/{pid_str}/stat", "rb") as handle:
                        raw = handle.read()
                    comm, ppid, state, used_ticks, threads, rss = _parse_stat(raw)
                except (OSError, ValueError, IndexError):
                    continue

                fresh_ticks[pid] = used_ticks
                name, user, is_kernel = self._static_info(pid, comm, ppid)
                if is_kernel and not include_kernel:
                    total_threads += threads
                    continue

                previous = ticks.get(pid)
                if previous is None or elapsed <= 0:
                    cpu = 0.0
                else:
                    cpu = (used_ticks - previous) / CLOCK_TICKS / elapsed * 100
                rss_bytes = rss * PAGE_SIZE
                rows.append(ProcRow(
                    pid=pid,
                    name=name,
                    user=user,
                    cpu=max(0.0, cpu),
                    mem_percent=rss_bytes / mem_total * 100,
                    rss=rss_bytes,
                    threads=threads,
                    status=_PROC_STATES.get(state, state),
                    vram=vram_map.get(pid, 0.0),
                ))
                total_threads += threads

        self._proc_ticks = fresh_ticks
        for pid in [p for p in self._proc_static if p not in fresh_ticks]:
            self._proc_static.pop(pid, None)
        return rows, total, total_threads

    def shutdown(self) -> None:
        self.stop_loop()
        self.gpu_backend.shutdown()


def kill_process(pid: int, force: bool = False) -> tuple[bool, str]:
    if pid <= 1 or pid == os.getpid():
        return False, f"Refusing to signal PID {pid}"
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        proc.kill() if force else proc.terminate()
        return True, f"Sent {'SIGKILL' if force else 'SIGTERM'} to {name} ({pid})"
    except psutil.NoSuchProcess:
        return False, f"Process {pid} no longer exists"
    except psutil.AccessDenied:
        return False, f"Permission denied for PID {pid}"
    except OSError as exc:
        return False, f"Failed to signal {pid}: {exc}"


def own_pid() -> int:
    return os.getpid()
