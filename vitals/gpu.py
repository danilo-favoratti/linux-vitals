"""GPU telemetry.

Reads NVIDIA metrics from a single long-lived ``nvidia-smi`` process running in
loop mode, which avoids paying process-spawn cost on every UI tick. The reader
lives on a background thread and publishes the latest snapshot behind a lock.
Falls back to a no-op backend when no supported GPU is present.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass, field

QUERY_FIELDS = [
    "utilization.gpu",
    "utilization.memory",
    "memory.used",
    "memory.total",
    "temperature.gpu",
    "power.draw",
    "fan.speed",
    "clocks.sm",
    "clocks.mem",
    "utilization.encoder",
    "utilization.decoder",
]


@dataclass
class GpuSample:
    name: str = "No GPU detected"
    available: bool = False
    util: float = 0.0          # core/3D utilization %
    mem_util: float = 0.0      # memory bus utilization %
    mem_used: float = 0.0      # bytes
    mem_total: float = 0.0     # bytes
    temperature: float = 0.0
    power: float = 0.0
    fan: float = 0.0
    clock_sm: float = 0.0
    clock_mem: float = 0.0
    encoder: float = 0.0
    decoder: float = 0.0
    driver: str = ""
    processes: dict[int, float] = field(default_factory=dict)  # pid -> VRAM bytes

    @property
    def mem_percent(self) -> float:
        if self.mem_total <= 0:
            return 0.0
        return self.mem_used / self.mem_total * 100


def _to_float(token: str) -> float:
    token = token.strip()
    try:
        return float(token)
    except ValueError:
        return 0.0  # covers "[N/A]" and "[Not Supported]"


class NvidiaBackend:
    """Streams `nvidia-smi` output on a daemon thread."""

    def __init__(self, interval_ms: int = 700) -> None:
        self._interval_ms = max(200, interval_ms)
        self._lock = threading.Lock()
        self._sample = GpuSample()
        self._proc: subprocess.Popen[str] | None = None
        self._stop = threading.Event()
        self._name = self._query_static("name")
        self._driver = self._query_static("driver_version")
        self._sample.name = self._name or "NVIDIA GPU"
        self._sample.driver = self._driver
        self._thread = threading.Thread(target=self._run, name="gpu-reader", daemon=True)
        self._thread.start()
        self._proc_thread = threading.Thread(
            target=self._run_processes, name="gpu-procs", daemon=True
        )
        self._proc_thread.start()

    @staticmethod
    def probe() -> bool:
        if not shutil.which("nvidia-smi"):
            return False
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return out.returncode == 0 and bool(out.stdout.strip())

    @staticmethod
    def _query_static(field_name: str) -> str:
        try:
            out = subprocess.run(
                ["nvidia-smi", f"--query-gpu={field_name}", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
            return out.stdout.strip().splitlines()[0].strip()
        except (OSError, subprocess.SubprocessError, IndexError):
            return ""

    def _run(self) -> None:
        cmd = [
            "nvidia-smi",
            "--query-gpu=" + ",".join(QUERY_FIELDS),
            "--format=csv,noheader,nounits",
            f"-lms={self._interval_ms}",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1,
            )
        except (OSError, subprocess.SubprocessError):
            return
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            parts = line.split(",")
            if len(parts) < len(QUERY_FIELDS):
                continue
            vals = [_to_float(p) for p in parts]
            with self._lock:
                s = self._sample
                s.available = True
                s.name = self._name or "NVIDIA GPU"
                s.driver = self._driver
                s.util = vals[0]
                s.mem_util = vals[1]
                s.mem_used = vals[2] * 1024 * 1024
                s.mem_total = vals[3] * 1024 * 1024
                s.temperature = vals[4]
                s.power = vals[5]
                s.fan = vals[6]
                s.clock_sm = vals[7]
                s.clock_mem = vals[8]
                s.encoder = vals[9]
                s.decoder = vals[10]

    def _run_processes(self) -> None:
        """Per-process VRAM needs a separate query; poll it more slowly."""
        while not self._stop.wait(2.0):
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-compute-apps=pid,used_memory",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            procs: dict[int, float] = {}
            for line in out.stdout.splitlines():
                parts = line.split(",")
                if len(parts) != 2:
                    continue
                try:
                    procs[int(parts[0])] = _to_float(parts[1]) * 1024 * 1024
                except ValueError:
                    continue
            with self._lock:
                self._sample.processes = procs

    def read(self) -> GpuSample:
        with self._lock:
            s = self._sample
            return GpuSample(
                name=s.name, available=s.available, util=s.util, mem_util=s.mem_util,
                mem_used=s.mem_used, mem_total=s.mem_total, temperature=s.temperature,
                power=s.power, fan=s.fan, clock_sm=s.clock_sm, clock_mem=s.clock_mem,
                encoder=s.encoder, decoder=s.decoder, driver=s.driver,
                processes=dict(s.processes),
            )

    def shutdown(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()


class NullBackend:
    """Used when no GPU telemetry source is available."""

    def __init__(self) -> None:
        self._sample = GpuSample()

    def read(self) -> GpuSample:
        return self._sample

    def shutdown(self) -> None:
        pass


def create_backend() -> NvidiaBackend | NullBackend:
    if NvidiaBackend.probe():
        return NvidiaBackend()
    return NullBackend()
