"""Check that a backgrounded web tab gives its web process back.

Four tabs each holding a live internal dashboard is what walked the long-running
process into an out-of-memory kill, so this asserts the suspend policy actually
tears the hidden ones down and that showing a tab again brings it back.

Needs a display; run it inside the desktop session.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vitals import bootstrap  # noqa: E402

bootstrap.add_vendor_typelibs()
bootstrap.install_logging()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from vitals import webpane  # noqa: E402
from vitals.config import TABS  # noqa: E402


def web_processes() -> int:
    out = subprocess.run(
        ["pgrep", "-c", "-P", str(os.getpid()), "-f", "WebKitWebProcess"],
        capture_output=True, text=True,
    )
    return int(out.stdout.strip() or 0)


def children() -> str:
    out = subprocess.run(
        ["ps", "--ppid", str(os.getpid()), "-o", "comm="],
        capture_output=True, text=True,
    )
    return " ".join(sorted(line.strip() for line in out.stdout.splitlines()))


def _descendants(root: int) -> list[int]:
    found, stack = [], [root]
    while stack:
        pid = stack.pop()
        try:
            for tid in os.listdir(f"/proc/{pid}/task"):
                with open(f"/proc/{pid}/task/{tid}/children") as handle:
                    for token in handle.read().split():
                        child = int(token)
                        found.append(child)
                        stack.append(child)
        except OSError:
            continue
    return found


def tree_rss_mb() -> int:
    """What the OOM killer weighs: this process plus every WebKit helper."""
    total = 0
    page = os.sysconf("SC_PAGE_SIZE")
    for pid in [os.getpid(), *_descendants(os.getpid())]:
        try:
            with open(f"/proc/{pid}/statm", "rb") as handle:
                total += int(handle.read().split()[1]) * page
        except (OSError, ValueError, IndexError):
            continue
    return total // 2**20


class Runner:
    def __init__(self) -> None:
        self.app = Adw.Application(application_id="dev.favoratti.VitalsTabTest")
        self.app.connect("activate", self._activate)
        self.failures: list[str] = []
        self.step = 0

    def _activate(self, app) -> None:
        self.window = Gtk.ApplicationWindow(application=app, default_width=700,
                                            default_height=500)
        self.pane = webpane.WebPane()
        self.window.set_child(self.pane)
        self.window.present()
        GLib.timeout_add_seconds(2, self._visit_all)

    def _visit_all(self) -> bool:
        """Load every tab, the state a real session ends up in."""
        keys = [key for key, *_ in TABS]
        if self.step < len(keys):
            self.pane.show_tab(keys[self.step])
            print(f"showing {keys[self.step]}")
            self.step += 1
            return True
        loaded = [t.title for t in self.pane.tabs.values() if t.loaded_at]
        print(f"\nafter visiting all {len(keys)} tabs: loaded={loaded}")
        print(f"  children: {children()}\n  tree rss: {tree_rss_mb()}M")
        if len(loaded) != len(keys):
            self.failures.append(f"expected all tabs loaded, got {loaded}")
        GLib.timeout_add_seconds(3, self._force_housekeeping)
        return False

    def _force_housekeeping(self) -> bool:
        # The real threshold is two minutes; pull it in so the test is quick.
        webpane.SUSPEND_AFTER = 0.0
        self.pane._housekeeping()
        current = self.pane.current
        still = [t.title for t in self.pane.tabs.values() if t.loaded_at]
        print(f"\nafter housekeeping: loaded={still} (visible={current.title})")
        if still != [current.title]:
            self.failures.append(
                f"only the visible tab should stay loaded, got {still}"
            )
        GLib.timeout_add_seconds(3, self._check_resume)
        return False

    def _check_resume(self) -> bool:
        print(f"  children: {children()}\n  tree rss: {tree_rss_mb()}M")
        victim = next(t for t in self.pane.tabs.values() if t is not self.pane.current)
        key = next(k for k, tab in self.pane.tabs.items() if tab is victim)
        self.pane.show_tab(key)
        if not victim.loaded_at:
            self.failures.append(f"{victim.title} did not reload when shown again")
        else:
            print(f"\n{victim.title} reloaded on being shown again: OK")
        GLib.timeout_add_seconds(2, self._finish)
        return False

    def _finish(self) -> bool:
        print(f"  children: {children()}\n  tree rss: {tree_rss_mb()}M")
        print(f"\nfds={bootstrap.fd_count()} own rss={bootstrap.rss_bytes() // 2**20}M tree rss={tree_rss_mb()}M")
        self.app.quit()
        return False


runner = Runner()
runner.app.run([])
if runner.failures:
    print("\nFAIL")
    for line in runner.failures:
        print(f"  - {line}")
    raise SystemExit(1)
print("\nPASS: hidden tabs are released and reload on demand")
