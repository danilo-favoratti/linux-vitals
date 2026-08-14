"""At-a-glance dashboard combining every subsystem."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .. import theme, ui  # noqa: E402
from ..sampler import Sampler, Snapshot  # noqa: E402
from ..widgets import Bars, Chart, Ring, SegmentBar, Trace  # noqa: E402


class OverviewPage(Gtk.Box):
    def __init__(self, sampler: Sampler) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.sampler = sampler
        root = ui.page_box()

        root.append(self._build_hero(sampler))
        root.append(self._build_cpu_card(sampler))

        grid = ui.ReflowGrid(min_child_width=330, choices=(2, 1))
        grid.add(self._build_memory_card(sampler))
        grid.add(self._build_network_card(sampler))
        grid.add(self._build_gpu_card(sampler))
        grid.add(self._build_disk_card(sampler))
        root.append(grid)

        self.append(ui.scroll(root))

    # -- construction ----------------------------------------------------

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
        card.header_end.append(self.mem_chip)

        self.mem_chart = Chart(
            [Trace(sampler.hist_mem, theme.MEM, "used"),
             Trace(sampler.hist_swap, theme.SWAP, "swap", fill=False)],
            percent=True, height=96,
        )
        card.append(self.mem_chart)
        self.mem_bar = SegmentBar()
        card.append(self.mem_bar)

        self.mem_stats = ui.StatGrid(columns=3)
        self.mem_stats.add("used", "Used")
        self.mem_stats.add("available", "Available")
        self.mem_stats.add("swap", "Swap used")
        self.mem_stats.set_margin_top(4)
        card.append(self.mem_stats)
        return card

    def _build_network_card(self, sampler: Sampler) -> Gtk.Widget:
        card = ui.Card("Network", accent=theme.NET_RX)
        self.net_chip = ui.chip("—")
        card.header_end.append(self.net_chip)

        self.net_chart = Chart(
            [Trace(sampler.hist_rx, theme.NET_RX, "down"),
             Trace(sampler.hist_tx, theme.NET_TX, "up")],
            percent=False, height=96, formatter=theme.fmt_rate,
        )
        self.net_chart.baseline_max = 64 * 1024
        card.append(self.net_chart)

        self.net_stats = ui.StatGrid(columns=2)
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
        card.header_end.append(self.gpu_chip)

        self.gpu_chart = Chart(
            [Trace(sampler.hist_gpu, theme.GPU, "core"),
             Trace(sampler.hist_vram, theme.VRAM, "vram", fill=False)],
            percent=True, height=96,
        )
        card.append(self.gpu_chart)

        self.gpu_stats = ui.StatGrid(columns=3)
        self.gpu_stats.add("util", "Core load")
        self.gpu_stats.add("vram", "Video memory")
        self.gpu_stats.add("temp", "Temperature")
        self.gpu_stats.set_margin_top(4)
        card.append(self.gpu_stats)
        return card

    def _build_disk_card(self, sampler: Sampler) -> Gtk.Widget:
        card = ui.Card("Disk I/O", accent=theme.DISK)
        self.disk_chip = ui.chip("—")
        card.header_end.append(self.disk_chip)

        self.disk_chart = Chart(
            [Trace(sampler.hist_disk_r, theme.DISK, "read"),
             Trace(sampler.hist_disk_w, theme.NET_TX, "write")],
            percent=False, height=96, formatter=theme.fmt_rate,
        )
        self.disk_chart.baseline_max = 1024 * 1024
        card.append(self.disk_chart)

        self.disk_stats = ui.StatGrid(columns=2)
        self.disk_stats.add("read", "Read")
        self.disk_stats.add("write", "Write")
        self.disk_stats.add("procs", "Processes")
        self.disk_stats.add("uptime", "Uptime")
        self.disk_stats.set_margin_top(4)
        card.append(self.disk_stats)
        return card

    # -- refresh ---------------------------------------------------------

    def update(self, s: Snapshot) -> None:
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
