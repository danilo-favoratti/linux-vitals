"""Start, stop and watch the sibling finances and health stacks.

Each app is a FastAPI backend plus a Vite frontend living next to this repo.
Vitals tracks the processes it spawned, but it also notices a stack already
running (for example from that project's ``run.sh``) so the buttons stay honest.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from gi.repository import GLib

from .config import LOCAL_APPS

DEV_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = Path(GLib.get_user_cache_dir()) / "vitals" / "apps"


def _secure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _resolved_root(rel: str) -> Path:
    root = (DEV_ROOT / rel).resolve()
    try:
        root.relative_to(DEV_ROOT)
    except ValueError as exc:
        raise RuntimeError(f"service root {rel!r} escapes {DEV_ROOT}") from exc
    return root


@dataclass(frozen=True)
class Service:
    key: str
    app: str
    title: str
    kind: str
    root: Path
    port: int
    api_origin: str
    open_url: str


@dataclass
class ServiceState:
    status: str = "stopped"  # stopped | starting | running | stopping | error
    pid: int | None = None
    ours: bool = False
    detail: str = ""
    since: float = 0.0


def _services() -> tuple[Service, ...]:
    out: list[Service] = []
    for spec in LOCAL_APPS:
        root = _resolved_root(spec["root"])
        backend = spec["backend_port"]
        frontend = spec["frontend_port"]
        origin = f"http://127.0.0.1:{backend}"
        open_url = f"http://127.0.0.1:{frontend}"
        for kind, port in (("backend", backend), ("frontend", frontend)):
            out.append(Service(
                key=f"{spec['key']}-{kind}",
                app=spec["key"],
                title=spec["title"],
                kind=kind,
                root=root,
                port=port,
                api_origin=origin,
                open_url=open_url,
            ))
    return tuple(out)


SERVICES = _services()
BY_KEY = {s.key: s for s in SERVICES}


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.15)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def listener_pids(port: int) -> list[int]:
    """PIDs listening on TCP ``port``.

    Forking ``lsof`` from the GTK thread after WebKit has started can stall
    the main loop (the child inherits held locks). This reads ``/proc`` instead.
    """
    hex_port = f"{port:04X}"
    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A":
                continue
            local = parts[1]
            if ":" not in local:
                continue
            if local.rsplit(":", 1)[1].upper() == hex_port:
                inodes.add(parts[9])
    if not inodes:
        return []
    found: list[int] = []
    try:
        pids = [int(name) for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return []
    for pid in pids:
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                found.append(pid)
                break
    return found


def _cwd_of(pid: int) -> Path | None:
    try:
        return Path(f"/proc/{pid}/cwd").resolve()
    except OSError:
        return None


def _belongs(pid: int, root: Path) -> bool:
    cwd = _cwd_of(pid)
    if cwd is None:
        return False
    try:
        cwd.relative_to(root)
        return True
    except ValueError:
        return False


def _which_npm() -> str | None:
    found = shutil.which("npm")
    if found:
        return found
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        for candidate in sorted(nvm.glob("*/bin/npm"), reverse=True):
            if candidate.is_file():
                return str(candidate)
    return None


def _last_log_line(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 4096))
            chunk = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    for line in reversed(chunk.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped[:180]
    return ""


class AppHub:
    """Owns the four local services and the processes Vitals started."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states = {s.key: ServiceState() for s in SERVICES}
        self._procs: dict[str, subprocess.Popen] = {}
        self._logs: dict[str, Path] = {}
        self._stop = threading.Event()
        try:
            _secure_dir(LOG_DIR)
        except OSError:
            pass
        threading.Thread(target=self._watch, name="app-hub", daemon=True).start()

    def snapshot(self) -> dict[str, ServiceState]:
        """Return a copy the UI can read. Port checks run on a worker thread."""
        with self._lock:
            return {
                key: ServiceState(
                    status=state.status, pid=state.pid,
                    ours=state.ours, detail=state.detail, since=state.since,
                )
                for key, state in self._states.items()
            }

    def _watch(self) -> None:
        while not self._stop.is_set():
            for service in SERVICES:
                try:
                    listening = port_open(service.port)
                except OSError:
                    listening = False
                with self._lock:
                    try:
                        self._reconcile(service, listening)
                    except Exception:
                        pass
            if self._stop.wait(1.0):
                break

    def toggle(self, key: str) -> str:
        """Start if down, stop if up. Returns a short toast."""
        service = BY_KEY.get(key)
        if service is None:
            return f"Unknown service {key}"
        try:
            listening = port_open(service.port)
        except OSError:
            listening = False
        with self._lock:
            self._reconcile(service, listening)
            state = self._states[key]
            if state.status in ("starting", "stopping"):
                return f"{service.title} {service.kind} is already {state.status}"
            if state.status == "running":
                return self._begin_stop(service)
            return self._begin_start(service)

    def shutdown(self) -> None:
        """Only the processes we spawned; a stack started by run.sh stays up."""
        self._stop.set()
        with self._lock:
            procs = list(self._procs.values())
            self._procs.clear()
        for proc in procs:
            self._kill_group(proc)

    def _reconcile(self, service: Service, listening: bool | None = None) -> None:
        state = self._states[service.key]
        proc = self._procs.get(service.key)
        alive = proc is not None and proc.poll() is None
        if listening is None:
            listening = port_open(service.port)

        if state.status == "starting":
            if listening:
                state.status = "running"
                state.pid = proc.pid if proc is not None else None
                state.ours = alive
                state.detail = f":{service.port}"
            elif proc is not None and proc.poll() is not None:
                state.status = "error"
                state.pid = None
                state.ours = False
                state.detail = _last_log_line(self._logs.get(service.key)) or "exited"
                self._procs.pop(service.key, None)
            elif state.since and time.monotonic() - state.since > 25:
                state.status = "error"
                state.detail = f"timed out waiting for :{service.port}"
            return

        if state.status == "stopping":
            if not listening and not alive:
                state.status = "stopped"
                state.pid = None
                state.ours = False
                state.detail = ""
                self._procs.pop(service.key, None)
            elif state.since and time.monotonic() - state.since > 6:
                state.status = "error"
                state.detail = f"still listening on :{service.port}"
            return

        if listening:
            state.status = "running"
            state.pid = proc.pid if alive else None
            state.ours = alive
            state.detail = f":{service.port}"
            return

        if state.status != "error":
            state.status = "stopped"
            state.pid = None
            state.ours = False
            if not alive:
                self._procs.pop(service.key, None)

    def _begin_start(self, service: Service) -> str:
        if not service.root.is_dir():
            self._states[service.key].status = "error"
            self._states[service.key].detail = f"{service.root} is missing"
            return f"{service.title} is not at {service.root}"
        self._states[service.key].status = "starting"
        self._states[service.key].detail = ""
        self._states[service.key].since = time.monotonic()
        threading.Thread(
            target=self._start_worker, args=(service,), daemon=True,
        ).start()
        return f"Starting {service.title} {service.kind}"

    def _start_worker(self, service: Service) -> None:
        try:
            proc = self._spawn(service)
        except Exception as exc:
            with self._lock:
                self._states[service.key].status = "error"
                self._states[service.key].detail = str(exc)[:180]
            return
        with self._lock:
            self._procs[service.key] = proc
            self._states[service.key].pid = proc.pid
            self._states[service.key].ours = True

    def _spawn(self, service: Service) -> subprocess.Popen:
        log_path = LOG_DIR / f"{service.key}.log"
        self._logs[service.key] = log_path
        try:
            _secure_dir(LOG_DIR)
            handle = open(log_path, "ab", buffering=0)
            os.chmod(log_path, 0o600)
        except OSError:
            handle = subprocess.DEVNULL
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        def fail(message: str) -> None:
            if handle is not subprocess.DEVNULL:
                try:
                    handle.write(message.encode())
                    handle.close()
                except OSError:
                    pass
            raise RuntimeError(message[:180])

        if service.kind == "backend":
            venv = service.root / "backend" / ".venv" / "bin"
            uvicorn = venv / "uvicorn"
            alembic = venv / "alembic"
            if not uvicorn.is_file():
                fail(f"no {uvicorn}; create the backend venv first")
            if alembic.is_file():
                upgrade = subprocess.run(
                    [str(alembic), "upgrade", "head"],
                    cwd=service.root / "backend",
                    env=env, capture_output=True, text=True, timeout=60,
                )
                if upgrade.returncode != 0:
                    fail(upgrade.stderr or upgrade.stdout or "alembic failed")
            cmd = [
                str(uvicorn), "app.main:app", "--reload",
                "--host", "127.0.0.1", "--port", str(service.port),
            ]
            cwd = service.root / "backend"
        else:
            npm = _which_npm()
            if npm is None:
                fail("npm is not on PATH")
            env["PATH"] = str(Path(npm).parent) + os.pathsep + env.get("PATH", "")
            frontend = service.root / "frontend"
            if not (frontend / "node_modules").is_dir():
                install = subprocess.run(
                    [npm, "install"], cwd=frontend, env=env,
                    capture_output=True, text=True, timeout=180,
                )
                if install.returncode != 0:
                    fail("npm install failed")
            env["DEV_PORT"] = str(service.port)
            env["API_ORIGIN"] = service.api_origin
            cmd = [
                npm, "run", "dev", "--",
                "--host", "127.0.0.1",
                "--port", str(service.port),
                "--strictPort",
            ]
            cwd = frontend

        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, env=env,
                stdout=handle, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            if handle is not subprocess.DEVNULL:
                try:
                    handle.close()
                except OSError:
                    pass
        return proc

    def _begin_stop(self, service: Service) -> str:
        state = self._states[service.key]
        state.status = "stopping"
        state.since = time.monotonic()
        proc = self._procs.get(service.key)
        threading.Thread(
            target=self._stop_worker, args=(service, proc), daemon=True,
        ).start()
        return f"Stopping {service.title} {service.kind}"

    def _stop_worker(
        self, service: Service, proc: subprocess.Popen | None,
    ) -> None:
        pids = listener_pids(service.port)
        if proc is not None:
            self._kill_group(proc)
        else:
            for pid in pids:
                if _belongs(pid, service.root):
                    self._kill_pid(pid)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if not port_open(service.port):
                break
            time.sleep(0.15)
        if port_open(service.port):
            leftover = listener_pids(service.port) or pids
            for pid in leftover:
                if _belongs(pid, service.root):
                    self._kill_pid(pid, force=True)
        with self._lock:
            self._procs.pop(service.key, None)

    def _kill_group(self, proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass

    def _kill_pid(self, pid: int, force: bool = False) -> None:
        if pid <= 1 or pid == os.getpid():
            return
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.kill(pid, sig)
        except (OSError, ProcessLookupError):
            pass
        try:
            pgid = os.getpgid(pid)
            if pgid != os.getpgid(0):
                os.killpg(pgid, sig)
        except (OSError, ProcessLookupError):
            pass
