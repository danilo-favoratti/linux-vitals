"""Bottom half of the window: the system monitor and its page switcher."""

from __future__ import annotations

import time
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .pages import (  # noqa: E402
    CpuPage,
    GpuPage,
    MemoryPage,
    NetworkPage,
    OverviewPage,
    ProcessPage,
)
from .sampler import Sampler, Snapshot  # noqa: E402
from . import apps, theme  # noqa: E402

PAGES = ("overview", "cpu", "memory", "network", "gpu", "processes")

# Pages that read from the process table and therefore need a fresh scan.
PROCESS_PAGES = ("processes", "cpu")


def _switcher(stack: Adw.ViewStack) -> Gtk.Widget:
    """Icon-only page switcher: the pane is far too narrow for labels."""
    if hasattr(Adw, "InlineViewSwitcher"):
        switcher = Adw.InlineViewSwitcher(stack=stack)
        switcher.set_display_mode(Adw.InlineViewSwitcherDisplayMode.ICONS)
        return switcher
    switcher = Adw.ViewSwitcher(stack=stack)
    switcher.set_policy(Adw.ViewSwitcherPolicy.NARROW)
    return switcher


class MonitorView(Gtk.Box):
    def __init__(self, sampler: Sampler, toast_overlay: Adw.ToastOverlay,
                 hub: apps.AppHub) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        # Carries the monitor's own surface colors, which the web half above it
        # does not share.
        self.add_css_class("monitor-pane")
        self.sampler = sampler

        self.stack = Adw.ViewStack()
        self.stack.set_vexpand(True)
        self.overview = OverviewPage(sampler, hub, toast_overlay)
        self.cpu_page = CpuPage(sampler)
        self.memory_page = MemoryPage(sampler)
        self.network_page = NetworkPage(sampler)
        self.gpu_page = GpuPage(sampler)
        self.process_page = ProcessPage(sampler, toast_overlay)

        for page, name, title, icon in (
            (self.overview, "overview", "Overview", "view-grid-symbolic"),
            (self.cpu_page, "cpu", "CPU", "speedometer-symbolic"),
            (self.memory_page, "memory", "Memory", "drive-harddisk-symbolic"),
            (self.network_page, "network", "Network", "network-wired-symbolic"),
            (self.gpu_page, "gpu", "GPU", "video-display-symbolic"),
            (self.process_page, "processes", "Processes", "view-list-symbolic"),
        ):
            self.stack.add_titled_with_icon(page, name, title, icon)

        self.pause_button = Gtk.ToggleButton(
            icon_name="media-playback-pause-symbolic", has_frame=False
        )
        self.pause_button.set_tooltip_text("Pause updates (Space)")

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("switcher-bar")
        bar.set_margin_start(6)
        bar.set_margin_end(6)
        switcher = _switcher(self.stack)
        switcher.set_hexpand(True)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.set_margin_top(4)
        switcher.set_margin_bottom(4)
        bar.append(switcher)
        bar.append(self.pause_button)

        footer = Gtk.CenterBox()
        footer.add_css_class("statusbar")
        footer.set_margin_start(10)
        footer.set_margin_end(10)
        footer.set_margin_top(3)
        footer.set_margin_bottom(3)

        self.status = Gtk.Label(label="", xalign=0)
        self.status.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        self.status.set_hexpand(True)

        self.self_stats = Gtk.Label(label="")
        self.self_stats.add_css_class("statusbar-self")
        self.self_stats.set_halign(Gtk.Align.CENTER)
        self.self_stats.set_tooltip_text(
            "Vitals CPU and RAM (this process + WebKit), and how long this session has been up"
        )
        self._self_cpu = 0.0
        self._self_rss = 0.0

        self.clock = Gtk.Label(xalign=1)
        self.clock.add_css_class("statusbar-clock")

        footer.set_start_widget(self.status)
        footer.set_center_widget(self.self_stats)
        footer.set_end_widget(self.clock)

        self.append(bar)
        self.append(self.stack)
        self.append(footer)
        self._update_clock()
        GLib.timeout_add_seconds(1, self._update_clock)

    # -- refresh ---------------------------------------------------------

    @property
    def page_name(self) -> str:
        return self.stack.get_visible_child_name() or "overview"

    @property
    def wants_processes(self) -> bool:
        return self.page_name in PROCESS_PAGES

    def show_page(self, name: str) -> None:
        if name in PAGES:
            self.stack.set_visible_child_name(name)

    def focus_search(self) -> None:
        self.show_page("processes")
        self.process_page.search.grab_focus()

    def update(self, snapshot: Snapshot, processes_fresh: bool) -> None:
        page = self.page_name
        self.overview.update(snapshot)
        if page == "cpu":
            self.cpu_page.update(snapshot)
        elif page == "memory":
            self.memory_page.update(snapshot)
        elif page == "network":
            self.network_page.update(snapshot)
        elif page == "gpu":
            self.gpu_page.update(snapshot)
        if processes_fresh and page == "processes":
            self.process_page.update(snapshot)
        self._self_cpu = snapshot.self_cpu
        self._self_rss = snapshot.self_rss
        self._paint_self()

    def set_status(self, text: str) -> None:
        self.status.set_label(text)

    def _paint_self(self) -> None:
        live = time.monotonic() - self.sampler.started_at
        self.self_stats.set_label(
            f"{self._self_cpu:.1f}% CPU  ·  {theme.fmt_bytes(self._self_rss)}  ·  "
            f"live {theme.fmt_live(live)}"
        )

    def _update_clock(self) -> bool:
        self.clock.set_label(datetime.now().strftime("%a %-d %b %Y  %H:%M:%S"))
        self._paint_self()
        return True
