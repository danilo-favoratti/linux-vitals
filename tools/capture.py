#!/usr/bin/env python3
"""Render the window to PNGs, one per web tab / monitor page combination.

Development helper used to eyeball the layout without a screenshot tool.
Usage: python3 tools/capture.py [output_dir]
"""

from __future__ import annotations

import pathlib
import sys

import gi

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

from vitals.app import VitalsApp, VitalsWindow  # noqa: E402
from vitals.sampler import Sampler  # noqa: E402

OUT_DIR = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/vitals-shots")
PAGES = ["overview", "cpu", "memory", "network", "gpu", "processes"]
PROCESS_FILTER = "sys"
WARMUP_MS = 7000
SETTLE_MS = 1400


def capture(window: Gtk.Window, path: pathlib.Path) -> bool:
    """Render the window to a texture and write it out as a PNG."""
    native = window.get_native()
    renderer = native.get_renderer() if native else None
    if renderer is None:
        return False
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, window.get_width(), window.get_height())
    node = snapshot.to_node()
    if node is None:
        return False
    renderer.render_texture(node, None).save_to_png(str(path))
    return True


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    app = VitalsApp()

    def on_activate(_app) -> None:
        app.sampler = Sampler()
        window = VitalsWindow(app, app.sampler)
        window.interval_ms = 500
        window._schedule()
        window.present()

        def step(name: str, page: str, tab: str | None = None):
            def run() -> None:
                window.monitor.show_page(page)
                if tab is not None and window.web is not None:
                    window.web.show_tab(tab)
                if page == "processes":
                    # Rendering thousands of bound rows to a texture yields an
                    # empty node, so narrow the list before snapshotting it.
                    window.monitor.process_page.search.set_text(PROCESS_FILTER)
            return name, run

        queue = [step("logs-overview", "overview", "logs")]
        queue += [step(f"dashboard-{page}", page, "dashboard") for page in PAGES]

        def next_shot() -> bool:
            if not queue:
                app.quit()
                return False
            name, prepare = queue.pop(0)
            prepare()

            def shoot(attempt: int = 1) -> bool:
                path = OUT_DIR / f"{name}.png"
                path.unlink(missing_ok=True)  # never report a stale file as fresh
                try:
                    ok = capture(window, path)
                except (GLib.Error, OSError) as exc:
                    ok = False
                    print(f"  capture error: {exc}")
                if not ok and attempt < 3:
                    GLib.timeout_add(600, shoot, attempt + 1)
                    return False
                print(f"saved {path}" if ok else f"FAILED {path}")
                GLib.timeout_add(200, next_shot)
                return False

            GLib.timeout_add(SETTLE_MS, shoot)
            return False

        GLib.timeout_add(WARMUP_MS, next_shot)

    app.connect("activate", on_activate)
    return app.run([])


if __name__ == "__main__":
    sys.exit(main())
