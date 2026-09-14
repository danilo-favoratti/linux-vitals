"""Makes the GTK4 WebKit bindings importable.

Ubuntu ships the library (``libwebkitgtk-6.0``) and its introspection data
(``gir1.2-webkit-6.0``) in separate packages, and the second one is not pulled
in by the desktop. When it is missing, ``tools/setup_webkit.sh`` unpacks the
typelibs into ``vendor/`` without needing root and this module points
GObject-Introspection at them.

The search path is registered on the introspection repository itself rather
than through ``GI_TYPELIB_PATH``, which GI only reads once, when its default
repository is created - by then an importing script may already have called
``gi.require_version`` for something else.
"""

from __future__ import annotations

import faulthandler
import glob
import os
import pathlib
import resource
import signal
import sys
import time

# Vulkan on NVIDIA + a rotated Wayland output (the portrait desk panel) often
# produces a surface that never paints. GTK 4.22's GL renderer is "gl"
# ("ngl" is the old name and only prints a warning). Must be set before GTK init.
if os.environ.get("GSK_RENDERER") in (None, "", "ngl"):
    os.environ["GSK_RENDERER"] = "gl"

_CACHE = pathlib.Path(
    os.environ.get("XDG_CACHE_HOME", str(pathlib.Path.home() / ".cache"))
) / "vitals"
LOG_PATH = _CACHE / "vitals.log"
_log_handle = None
_TARGET_NOFILE = 65536
_FD_BUDGET = 2048
# A fresh session with one tab open sits near 1.1 GB, almost all of it the web
# process for the dashboard. This leaves room to breathe while still catching
# growth long before the tree becomes the session's biggest target.
MEMORY_BUDGET = 1900 * 1024 * 1024

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR_TYPELIB_DIR = PROJECT_ROOT / "vendor" / "girepository-1.0"
TYPELIB = "WebKit-6.0.typelib"


def _installed_system_wide() -> bool:
    return any(
        glob.glob(f"/usr/lib*/girepository-1.0/{TYPELIB}")
        + glob.glob(f"/usr/lib/*/girepository-1.0/{TYPELIB}")
    )


def _repository():
    import gi

    for holder in (gi, getattr(gi, "_gi", None)):
        getter = getattr(getattr(holder, "Repository", None), "get_default", None)
        if getter is not None:
            return getter()
    return None


def add_vendor_typelibs() -> None:
    """Add ``vendor/`` to the typelib search path when the distro lacks it."""
    if _installed_system_wide() or not (VENDOR_TYPELIB_DIR / TYPELIB).exists():
        return

    repository = _repository()
    if repository is not None:
        repository.prepend_search_path(str(VENDOR_TYPELIB_DIR))

    # Child processes (WebKit's helpers, dev tools) get it through the
    # environment.
    existing = [p for p in os.environ.get("GI_TYPELIB_PATH", "").split(os.pathsep) if p]
    if str(VENDOR_TYPELIB_DIR) not in existing:
        os.environ["GI_TYPELIB_PATH"] = os.pathsep.join(
            [*existing, str(VENDOR_TYPELIB_DIR)]
        )


def webkit_available() -> bool:
    import gi

    try:
        gi.require_version("WebKit", "6.0")
    except ValueError:
        return False
    return True


def raise_nofile() -> None:
    """Lift the 1024 soft cap. WebKit aborts the process if spawn hits EMFILE.

    The soft limit is deliberately capped rather than pushed all the way to the
    hard limit (524288 here): plenty of library code still sizes arrays by
    RLIMIT_NOFILE or closes descriptors by counting up to it.
    """
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = _TARGET_NOFILE if hard < 0 else min(hard, _TARGET_NOFILE)
        if want > soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except (OSError, ValueError):
        pass


def fd_count() -> int:
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except OSError:
        return 0


def fd_breakdown(top: int = 6) -> str:
    """The commonest descriptor targets, so a leak names itself in the log."""
    fd_dir = f"/proc/{os.getpid()}/fd"
    tally: dict[str, int] = {}
    try:
        names = os.listdir(fd_dir)
    except OSError:
        return ""
    for name in names:
        try:
            target = os.readlink(f"{fd_dir}/{name}")
        except OSError:
            continue
        # Collapse the varying part so identical kinds group together.
        if target.startswith(("socket:", "pipe:", "anon_inode:")):
            target = target.split("[")[0].rstrip(":")
        tally[target] = tally.get(target, 0) + 1
    ranked = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return " ".join(f"{count}x{target}" for target, count in ranked)


def rss_bytes() -> int:
    try:
        with open(f"/proc/{os.getpid()}/statm", "rb") as handle:
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


def memory_pressure(total_bytes: float) -> bool:
    """True once this process tree is fat enough to be worth killing.

    Ubuntu runs ``user@.service`` with ``ManagedOOMMemoryPressure=kill``, so
    systemd-oomd SIGKILLs the whole scope through ``cgroup.kill`` when the
    session comes under memory pressure - no signal we can catch, no log line,
    no core dump. The only defence is not being the biggest thing in the
    session, which means reloading the web views before they get there.

    The caller passes the figure for this process plus its WebKit helpers.
    Reading the cgroup would be the same number only when the desktop put
    Vitals in a scope of its own; started any other way it inherits the
    launching app's cgroup and reads back that app's memory instead.
    """
    return total_bytes >= MEMORY_BUDGET


def fd_limit() -> int:
    try:
        return resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    except (OSError, ValueError):
        return 1024


def fd_pressure() -> bool:
    """True well before the limit bites.

    A healthy session sits under a hundred descriptors, so anything past the
    budget means something is accumulating and is worth reclaiming now rather
    than at 85% of 65536, by which point the leak has been running for months.
    """
    limit = fd_limit()
    ceiling = _FD_BUDGET if limit <= 0 else min(int(limit * 0.85), _FD_BUDGET)
    return fd_count() >= ceiling


def install_logging() -> None:
    """Duplicate stderr into ~/.cache/vitals/vitals.log and catch native crashes."""
    global _log_handle
    try:
        _CACHE.mkdir(parents=True, exist_ok=True)
        os.chmod(_CACHE, 0o700)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
            LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))
        _log_handle = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
        os.chmod(LOG_PATH, 0o600)
    except OSError:
        return
    faulthandler.enable(file=_log_handle, all_threads=True)
    sys.stderr = _Tee(sys.stderr, _log_handle)
    sys.excepthook = _excepthook
    raise_nofile()
    _log_termination_signals()
    log(
        f"start pid={os.getpid()} GSK_RENDERER={os.environ.get('GSK_RENDERER')!r} "
        f"nofile={fd_limit()} fds={fd_count()}"
    )


def _log_termination_signals() -> None:
    """Name the signal that ends the process.

    faulthandler already covers the crash signals, so a process that vanished
    without a word was asked to leave. Recording which signal arrived separates
    a session/logout SIGTERM from an out-of-memory SIGKILL, which cannot be
    caught and therefore shows up as silence.
    """
    def handler(signum, _frame):
        log(f"received {signal.Signals(signum).name}, exiting")
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for name in ("SIGTERM", "SIGHUP", "SIGINT"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            signal.signal(number, handler)
        except (OSError, ValueError):
            pass


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n"
    handle = _log_handle
    if handle is None:
        return
    try:
        handle.write(line)
        handle.flush()
    except OSError:
        pass


class _Tee:
    def __init__(self, primary, secondary) -> None:
        self._primary = primary
        self._secondary = secondary

    def write(self, data) -> int:
        try:
            self._primary.write(data)
        except OSError:
            pass
        try:
            self._secondary.write(data)
        except OSError:
            pass
        return len(data) if isinstance(data, str) else 0

    def flush(self) -> None:
        for stream in (self._primary, self._secondary):
            try:
                stream.flush()
            except OSError:
                pass

    def fileno(self) -> int:
        return self._primary.fileno()


def _excepthook(exc_type, exc, tb) -> None:
    import traceback
    log("unhandled exception:\n" + "".join(
        traceback.format_exception(exc_type, exc, tb)
    ))
    sys.__excepthook__(exc_type, exc, tb)


MISSING_WEBKIT_HINT = (
    "WebKit for GTK4 is not available.\n\n"
    "Install the introspection data system-wide:\n"
    "    sudo apt install gir1.2-webkit-6.0\n\n"
    "or unpack it locally without root:\n"
    "    ./tools/setup_webkit.sh"
)
