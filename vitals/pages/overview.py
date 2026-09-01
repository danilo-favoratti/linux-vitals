"""At-a-glance dashboard combining every subsystem."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import apps, theme, ui  # noqa: E402
from ..config import LOCAL_APPS  # noqa: E402
from ..sampler import Sampler, Snapshot  # noqa: E402
from ..widgets import Bars, Chart, Ring, SegmentBar, Trace  # noqa: E402

_STATUS_CLASS = {
    "running": "app-dot-running",
    "starting": "app-dot-starting",
    "stopping": "app-dot-stopping",
    "error": "app-dot-error",
}


class OverviewPage(Gtk.Box):
    def __init__(self, sampler: Sampler, hub: apps.AppHub,
                 toast_overlay: Adw.ToastOverlay) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.sampler = sampler
        self.hub = hub
        self.toast_overlay = toast_overlay
        root = ui.page_box()

        root.append(self._build_apps())
        root.append(self._build_hero(sampler))

        metrics = Gtk.Grid(column_spacing=12, row_spacing=12)
        metrics.set_column_homogeneous(True)
        cpu = self._build_cpu_card(sampler)
        gpu = self._build_gpu_card(sampler)
        mem = self._build_memory_card(sampler)
        net = self._build_network_card(sampler)
        disk = self._build_disk_card(sampler)
        for card in (cpu, gpu, mem, net, disk):
            card.set_hexpand(True)
        # Processor 2/3, video processor 1/3; the other three share the next row.
        metrics.attach(cpu, 0, 0, 2, 1)
        metrics.attach(gpu, 2, 0, 1, 1)
        metrics.attach(mem, 0, 1, 1, 1)
        metrics.attach(disk, 1, 1, 1, 1)
        metrics.attach(net, 2, 1, 1, 1)
        root.append(metrics)

        self.append(ui.scroll(root))

    # -- construction ----------------------------------------------------

    def _build_apps(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_homogeneous(True)
        self._app_tiles: list[_AppTile] = []
        for spec in LOCAL_APPS:
            tile = _AppTile(spec, self.hub, self._open_app, self._toast)
            tile.set_hexpand(True)
            self._app_tiles.append(tile)
            row.append(tile)
        return row

    def _open_app(self, key: str, title: str, url: str) -> None:
        state = self.hub.snapshot().get(f"{key}-frontend")
        if state is None or state.status != "running":
            self._toast(f"Start {title}’s website first")
            return
        Gtk.UriLauncher(uri=url).launch(
            self.get_root() if isinstance(self.get_root(), Gtk.Window) else None,
            None, None,
        )
        self._toast(f"Opening {title}")

    def _toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=2))

    def _build_hero(self, sampler: Sampler) -> Gtk.Widget:
        # Four across on a wide window, two-by-two in the portrait split.
        grid = ui.ReflowGrid(min_child_width=152, spacing=12, choices=(4, 2))
        self.rings: dict[str, Ring] = {}
        specs = [
            ("cpu", "CPU", theme.CPU),
            ("mem", "MEMORY", theme.MEM),
            ("gpu", "GPU", theme.GPU),
            ("vram", "VIDEO MEMORY", theme.VRAM),
        ]
        for key, caption, color in specs:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            card.add_css_class("card")
            ring = Ring(color, size=118, caption=caption)
            ring.set_margin_top(12)
            ring.set_margin_bottom(8)
            ring.set_halign(Gtk.Align.CENTER)
            card.append(ring)
            self.rings[key] = ring
            grid.add(card)
        return grid

    def _build_cpu_card(self, sampler: Sampler) -> Gtk.Widget:
        card = ui.Card("Processor", accent=theme.CPU)
        self.cpu_chip_freq = ui.chip("—")
        self.cpu_chip_temp = ui.chip("—")
        self.cpu_chip_load = ui.chip("—")
        for c in (self.cpu_chip_load, self.cpu_chip_temp, self.cpu_chip_freq):
            c.set_ellipsize(ui.ELLIPSIZE_END)
            card.header_end.append(c)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        left.set_valign(Gtk.Align.CENTER)
        box, self.cpu_value, _ = ui.big_value("0", "%")
        left.append(box)
        sub = Gtk.Label(label=f"{sampler.core_count} threads", xalign=0)
        sub.add_css_class("metric-sub")
        left.append(sub)
        left.set_size_request(96, -1)
        row.append(left)

        self.cpu_chart = Chart(
            [Trace(sampler.hist_cpu, theme.CPU, "total")], percent=True, height=104
        )
        row.append(self.cpu_chart)
        card.append(row)

        self.core_bars = Bars(height=62)
        self.core_bars.set_margin_top(4)
        card.append(self.core_bars)
        return card

    def _build_memory_card(self, sampler: Sampler) -> Gtk.Widget:
        card = ui.Card("Memory & Swap", accent=theme.MEM)
        self.mem_chip = ui.chip("—")
        self.mem_chip.set_ellipsize(ui.ELLIPSIZE_END)
        card.header_end.append(self.mem_chip)

        self.mem_chart = Chart(
            [Trace(sampler.hist_mem, theme.MEM, "used"),
             Trace(sampler.hist_swap, theme.SWAP, "swap", fill=False)],
            percent=True, height=96,
        )
        card.append(self.mem_chart)
        self.mem_bar = SegmentBar()
        card.append(self.mem_bar)

        self.mem_stats = ui.StatGrid(columns=3, min_cell_width=72)
        self.mem_stats.add("used", "Used")
        self.mem_stats.add("available", "Available")
        self.mem_stats.add("swap", "Swap used")
        self.mem_stats.set_margin_top(4)
        card.append(self.mem_stats)
        return card

    def _build_network_card(self, sampler: Sampler) -> Gtk.Widget:
        card = ui.Card("Network", accent=theme.NET_RX)
        self.net_chip = ui.chip("—")
        self.net_chip.set_ellipsize(ui.ELLIPSIZE_END)
        card.header_end.append(self.net_chip)

        self.net_chart = Chart(
            [Trace(sampler.hist_rx, theme.NET_RX, "down"),
             Trace(sampler.hist_tx, theme.NET_TX, "up")],
            percent=False, height=96, formatter=theme.fmt_rate,
        )
        self.net_chart.baseline_max = 64 * 1024
        card.append(self.net_chart)

        self.net_stats = ui.StatGrid(columns=2, min_cell_width=72)
        self.net_stats.add("rx", "↓ Download")
        self.net_stats.add("tx", "↑ Upload")
        self.net_stats.add("rx_total", "Total received")
        self.net_stats.add("tx_total", "Total sent")
        self.net_stats.set_margin_top(4)
        card.append(self.net_stats)
        return card

    def _build_gpu_card(self, sampler: Sampler) -> Gtk.Widget:
        card = ui.Card("Video Processor", accent=theme.GPU)
        self.gpu_chip = ui.chip("—")
        self.gpu_chip.set_ellipsize(ui.ELLIPSIZE_END)
        card.header_end.append(self.gpu_chip)

        self.gpu_chart = Chart(
            [Trace(sampler.hist_gpu, theme.GPU, "core"),
             Trace(sampler.hist_vram, theme.VRAM, "vram", fill=False)],
            percent=True, height=96,
        )
        card.append(self.gpu_chart)

        self.gpu_stats = ui.StatGrid(columns=3, min_cell_width=72)
        self.gpu_stats.add("util", "Core load")
        self.gpu_stats.add("vram", "Video memory")
        self.gpu_stats.add("temp", "Temperature")
        self.gpu_stats.set_margin_top(4)
        card.append(self.gpu_stats)
        return card

    def _build_disk_card(self, sampler: Sampler) -> Gtk.Widget:
        card = ui.Card("Disk I/O", accent=theme.DISK)
        self.disk_chip = ui.chip("—")
        self.disk_chip.set_ellipsize(ui.ELLIPSIZE_END)
        card.header_end.append(self.disk_chip)

        self.disk_chart = Chart(
            [Trace(sampler.hist_disk_r, theme.DISK, "read"),
             Trace(sampler.hist_disk_w, theme.NET_TX, "write")],
            percent=False, height=96, formatter=theme.fmt_rate,
        )
        self.disk_chart.baseline_max = 1024 * 1024
        card.append(self.disk_chart)

        self.disk_stats = ui.StatGrid(columns=2, min_cell_width=72)
        self.disk_stats.add("read", "Read")
        self.disk_stats.add("write", "Write")
        self.disk_stats.add("procs", "Processes")
        self.disk_stats.add("uptime", "Uptime")
        self.disk_stats.set_margin_top(4)
        card.append(self.disk_stats)
        return card

    # -- refresh ---------------------------------------------------------

    def update(self, s: Snapshot) -> None:
        self._refresh_apps()
        self.rings["cpu"].update(
            s.cpu_total, f"{s.cpu_total:.0f}%",
            theme.fmt_hz(s.cpu_freq_avg) if s.cpu_freq_avg else "",
        )
        self.rings["mem"].update(
            s.mem_percent, f"{s.mem_percent:.0f}%",
            f"{theme.fmt_bytes(s.mem_used)} / {theme.fmt_bytes(s.mem_total)}",
        )
        gpu = s.gpu
        if gpu.available:
            self.rings["gpu"].update(gpu.util, f"{gpu.util:.0f}%",
                                     f"{gpu.temperature:.0f}°C")
            self.rings["vram"].update(
                gpu.mem_percent, f"{gpu.mem_percent:.0f}%",
                f"{theme.fmt_bytes(gpu.mem_used)} / {theme.fmt_bytes(gpu.mem_total)}",
            )
        else:
            self.rings["gpu"].update(0, "n/a", "no GPU")
            self.rings["vram"].update(0, "n/a", "no GPU")

        self.cpu_value.set_label(f"{s.cpu_total:.1f}")
        self.cpu_chart.queue_draw()
        self.core_bars.update(s.cpu_per_core)
        self.cpu_chip_load.set_label(
            f"load {s.load_avg[0]:.2f} · {s.load_avg[1]:.2f} · {s.load_avg[2]:.2f}"
        )
        self.cpu_chip_temp.set_label(
            f"{s.cpu_temp:.0f}°C" if s.cpu_temp else "temp n/a"
        )
        self.cpu_chip_freq.set_label(
            theme.fmt_hz(s.cpu_freq_avg) if s.cpu_freq_avg else "—"
        )

        self.mem_chart.queue_draw()
        app_mem = max(0.0, s.mem_used - s.mem_cached - s.mem_buffers)
        self.mem_bar.update([
            ("Apps", app_mem, theme.MEM),
            ("Cache", s.mem_cached + s.mem_buffers, theme.MEM_CACHE),
            ("Free", s.mem_available, theme.BG_CARD_ALT),
        ], s.mem_total)
        self.mem_stats.set("used", f"{theme.fmt_bytes(s.mem_used)}")
        self.mem_stats.set("available", theme.fmt_bytes(s.mem_available))
        self.mem_stats.set(
            "swap",
            f"{theme.fmt_bytes(s.swap_used)} ({s.swap_percent:.0f}%)"
            if s.swap_total else "disabled",
        )
        self.mem_chip.set_label(f"{s.mem_percent:.1f}% of {theme.fmt_bytes(s.mem_total)}")

        self.net_chart.queue_draw()
        self.net_stats.set("rx", theme.fmt_rate(s.net_rx_rate))
        self.net_stats.set("tx", theme.fmt_rate(s.net_tx_rate))
        self.net_stats.set("rx_total", theme.fmt_bytes(s.net_rx_total))
        self.net_stats.set("tx_total", theme.fmt_bytes(s.net_tx_total))
        self.net_chip.set_label(
            f"↓ {theme.fmt_rate(s.net_rx_rate)}  ↑ {theme.fmt_rate(s.net_tx_rate)}"
        )

        self.gpu_chart.queue_draw()
        if gpu.available:
            self.gpu_stats.set("util", f"{gpu.util:.0f}%")
            self.gpu_stats.set(
                "vram", f"{theme.fmt_bytes(gpu.mem_used)} ({gpu.mem_percent:.0f}%)"
            )
            self.gpu_stats.set("temp", f"{gpu.temperature:.0f}°C")
            self.gpu_chip.set_label(gpu.name.replace("NVIDIA ", ""))
        else:
            for key in ("util", "vram", "temp"):
                self.gpu_stats.set(key, "n/a")
            self.gpu_chip.set_label("not detected")

        self.disk_chart.queue_draw()
        self.disk_stats.set("read", theme.fmt_rate(s.disk_read_rate))
        self.disk_stats.set("write", theme.fmt_rate(s.disk_write_rate))
        self.disk_stats.set("procs", f"{s.proc_count} ({s.thread_count} thr)")
        self.disk_stats.set("uptime", theme.fmt_secs(s.uptime))
        self.disk_chip.set_label(
            f"R {theme.fmt_rate(s.disk_read_rate)}  W {theme.fmt_rate(s.disk_write_rate)}"
        )

    def _refresh_apps(self) -> None:
        snap = self.hub.snapshot()
        for tile in self._app_tiles:
            tile.apply(snap)


_CHIP_CLASS = {
    "running": "app-chip-running",
    "starting": "app-chip-busy",
    "stopping": "app-chip-busy",
    "error": "app-chip-error",
}

_STATUS_LABEL = {
    "running": "RUNNING",
    "starting": "STARTING",
    "stopping": "STOPPING",
    "stopped": "STOPPED",
    "error": "ERROR",
}

_ROLES = (
    ("backend", "API", "network-server-symbolic", "backend_port"),
    ("frontend", "Website", "web-browser-symbolic", "frontend_port"),
)


_APP_ACCENT = {
    "finances": theme.CPU,
    "health": theme.MEM,
}


class _AppTile(ui.Card):
    """One sibling project as a top-level card: API + website + open."""

    def __init__(self, spec: dict, hub: apps.AppHub, open_app, toast) -> None:
        super().__init__(
            spec["title"],
            accent=_APP_ACCENT.get(spec["key"], theme.CPU),
        )
        self.key = spec["key"]
        self.title = spec["title"]
        self.hub = hub
        self._toast = toast

        self.summary = Gtk.Label(label="STOPPED")
        self.summary_box = Gtk.Box()
        self.summary_box.add_css_class("app-chip")
        self.summary_box.set_valign(Gtk.Align.CENTER)
        self.summary_box.append(self.summary)
        self.header_end.append(self.summary_box)

        self.rows: list[_ServiceRow] = []
        for kind, role, icon, port_key in _ROLES:
            row = _ServiceRow(
                f"{spec['key']}-{kind}", role, icon, spec[port_key], hub, toast,
            )
            self.rows.append(row)
            self.append(row)

        self.open_btn = Gtk.Button()
        self.open_btn.add_css_class("app-open")
        self.open_btn.set_hexpand(True)
        self.open_btn.set_child(Adw.ButtonContent(
            icon_name="web-browser-symbolic", label="Open in browser",
        ))
        url = f"http://127.0.0.1:{spec['frontend_port']}"
        self.open_btn.connect(
            "clicked", lambda *_: open_app(spec["key"], spec["title"], url),
        )
        self.append(self.open_btn)

    def apply(self, snap: dict[str, apps.ServiceState]) -> None:
        states = [snap.get(row.key) for row in self.rows]
        for row, state in zip(self.rows, states):
            row.apply(state)
        statuses = [s.status if s else "stopped" for s in states]
        site_up = statuses[1] == "running"
        self.open_btn.set_sensitive(site_up)
        self.open_btn.set_tooltip_text(
            f"Open {self.title} at its website port"
            if site_up else f"Start the {self.title} website first"
        )
        if all(s == "running" for s in statuses):
            text, klass = "READY", "app-chip-running"
        elif any(s == "error" for s in statuses):
            text, klass = "ERROR", "app-chip-error"
        elif any(s in ("starting", "stopping") for s in statuses):
            text, klass = "WORKING", "app-chip-busy"
        elif any(s == "running" for s in statuses):
            text, klass = "PARTIAL", "app-chip-busy"
        else:
            text, klass = "STOPPED", ""
        self.summary.set_label(text)
        for name in ("app-chip-running", "app-chip-busy", "app-chip-error"):
            self.summary_box.remove_css_class(name)
        if klass:
            self.summary_box.add_css_class(klass)
        if site_up:
            self.open_btn.add_css_class("app-open-ready")
        else:
            self.open_btn.remove_css_class("app-open-ready")


class _ServiceRow(Gtk.Box):
    """One process in a stack: what it is, whether it is up, start or stop."""

    def __init__(self, key: str, role: str, icon: str, port: int,
                 hub: apps.AppHub, toast) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("app-service")
        self.key = key
        self.port = port
        self.hub = hub
        self._toast = toast
        self._status = "stopped"

        self.dot = Gtk.Box()
        self.dot.set_size_request(9, 9)
        self.dot.set_valign(Gtk.Align.CENTER)
        self.dot.add_css_class("app-dot")

        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        copy.set_hexpand(True)
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title.append(Gtk.Image(icon_name=icon))
        role_label = Gtk.Label(label=role, xalign=0)
        role_label.add_css_class("app-role")
        title.append(role_label)
        self.port_label = Gtk.Label(label=f"port {port}", xalign=0)
        self.port_label.add_css_class("app-port")
        self.port_label.set_ellipsize(ui.ELLIPSIZE_END)
        copy.append(title)
        copy.append(self.port_label)

        self.chip = Gtk.Label(label="STOPPED")
        self.chip_box = Gtk.Box()
        self.chip_box.add_css_class("app-chip")
        self.chip_box.set_valign(Gtk.Align.CENTER)
        self.chip_box.append(self.chip)

        self.button = Gtk.Button()
        self.button.add_css_class("circular")
        self.button.add_css_class("app-toggle")
        self.button.add_css_class("app-toggle-start")
        self.button.set_valign(Gtk.Align.CENTER)
        self.button.set_icon_name("media-playback-start-symbolic")
        self.button.connect("clicked", self._on_clicked)

        self.append(self.dot)
        self.append(copy)
        self.append(self.chip_box)
        self.append(self.button)

    def apply(self, state: apps.ServiceState | None) -> None:
        if state is None:
            return
        self._status = state.status
        running = state.status == "running"
        busy = state.status in ("starting", "stopping")
        self.button.set_sensitive(not busy)
        stopping = state.status == "stopping"
        self.button.set_icon_name(
            "media-playback-stop-symbolic" if running or stopping
            else "media-playback-start-symbolic"
        )
        self.button.set_tooltip_text(
            "Stop this process" if running or stopping
            else f"Start this process on port {self.port}"
        )
        if running or stopping:
            self.button.add_css_class("app-toggle-stop")
            self.button.remove_css_class("app-toggle-start")
        else:
            self.button.remove_css_class("app-toggle-stop")
            self.button.add_css_class("app-toggle-start")
        self.remove_css_class("app-service-running")
        self.remove_css_class("app-service-busy")
        self.remove_css_class("app-service-error")
        if running:
            self.add_css_class("app-service-running")
        elif state.status in ("starting", "stopping"):
            self.add_css_class("app-service-busy")
        elif state.status == "error":
            self.add_css_class("app-service-error")
        for name in _STATUS_CLASS.values():
            self.dot.remove_css_class(name)
        klass = _STATUS_CLASS.get(state.status)
        if klass:
            self.dot.add_css_class(klass)
        self.chip.set_label(_STATUS_LABEL.get(state.status, state.status.upper()))
        for name in _CHIP_CLASS.values():
            self.chip_box.remove_css_class(name)
        chip_klass = _CHIP_CLASS.get(state.status)
        if chip_klass:
            self.chip_box.add_css_class(chip_klass)
        if state.status == "error" and state.detail:
            self.port_label.set_label(state.detail)
            self.port_label.set_tooltip_text(state.detail)
        else:
            self.port_label.set_label(f"port {self.port}")
            self.port_label.set_tooltip_text(None)

    def _on_clicked(self, *_args) -> None:
        self._toast(self.hub.toggle(self.key))

