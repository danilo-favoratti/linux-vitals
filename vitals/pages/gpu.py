"""Video processor and video memory detail page."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .. import theme, ui  # noqa: E402
from ..sampler import Sampler, Snapshot  # noqa: E402
from ..widgets import Chart, Ring, SegmentBar, Trace  # noqa: E402


class GpuPage(Gtk.Box):
    def __init__(self, sampler: Sampler) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.sampler = sampler
        root = ui.page_box()

        self.banner = Gtk.Label(
            label="No supported GPU telemetry found. Install the NVIDIA driver "
                  "(nvidia-smi) for video metrics.",
            xalign=0,
        )
        self.banner.add_css_class("metric-sub")
        self.banner.set_wrap(True)
        self.banner.set_visible(False)
        root.append(self.banner)

        proc = ui.Card("Video Processor", accent=theme.GPU)
        self.name_chip = ui.chip("—")
        proc.header_end.append(self.name_chip)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.gpu_ring = Ring(theme.GPU, size=132, caption="CORE LOAD")
        self.gpu_ring.set_valign(Gtk.Align.CENTER)
        top.append(self.gpu_ring)
        self.gpu_chart = Chart(
            [Trace(sampler.hist_gpu, theme.GPU)], percent=True, height=132
        )
        top.append(self.gpu_chart)
        proc.append(top)
        self.gpu_stats = ui.StatGrid(columns=4)
        for key, caption in (
            ("temp", "Temperature"), ("power", "Power draw"),
            ("fan", "Fan speed"), ("clock", "Core clock"),
            ("encoder", "Encoder load"), ("decoder", "Decoder load"),
            ("mem_bus", "Memory bus load"), ("driver", "Driver"),
        ):
            self.gpu_stats.add(key, caption)
        self.gpu_stats.set_margin_top(6)
        proc.append(self.gpu_stats)
        root.append(proc)

        vram = ui.Card("Video Memory", accent=theme.VRAM)
        top2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.vram_ring = Ring(theme.VRAM, size=132, caption="ALLOCATED")
        self.vram_ring.set_valign(Gtk.Align.CENTER)
        top2.append(self.vram_ring)
        self.vram_chart = Chart(
            [Trace(sampler.hist_vram, theme.VRAM)], percent=True, height=132
        )
        top2.append(self.vram_chart)
        vram.append(top2)
        self.vram_bar = SegmentBar(height=40)
        vram.append(self.vram_bar)
        self.vram_stats = ui.StatGrid(columns=4)
        for key, caption in (
            ("total", "Total"), ("used", "Used"),
            ("free", "Free"), ("clock", "Memory clock"),
        ):
            self.vram_stats.add(key, caption)
        self.vram_stats.set_margin_top(6)
        vram.append(self.vram_stats)
        root.append(vram)

        clients = ui.Card("Processes Using the GPU", accent=theme.VRAM)
        self.client_empty = Gtk.Label(label="No processes are allocating video memory.",
                                      xalign=0)
        self.client_empty.add_css_class("metric-sub")
        self.clients = ui.TopList(theme.VRAM, rows=12)
        clients.append(self.client_empty)
        clients.append(self.clients)
        root.append(clients)

        self.append(ui.scroll(root))

    def update(self, s: Snapshot) -> None:
        gpu = s.gpu
        if not gpu.available:
            self.banner.set_visible(True)
            self.name_chip.set_label("not detected")
            self.gpu_ring.update(0, "n/a")
            self.vram_ring.update(0, "n/a")
            return
        self.banner.set_visible(False)
        self.name_chip.set_label(gpu.name)

        self.gpu_ring.update(gpu.util, f"{gpu.util:.0f}%", f"{gpu.temperature:.0f}°C")
        self.gpu_chart.queue_draw()
        self.gpu_stats.set("temp", f"{gpu.temperature:.0f} °C")
        self.gpu_stats.set("power", f"{gpu.power:.1f} W")
        self.gpu_stats.set("fan", f"{gpu.fan:.0f} %")
        self.gpu_stats.set("clock", theme.fmt_hz(gpu.clock_sm))
        self.gpu_stats.set("encoder", f"{gpu.encoder:.0f} %")
        self.gpu_stats.set("decoder", f"{gpu.decoder:.0f} %")
        self.gpu_stats.set("mem_bus", f"{gpu.mem_util:.0f} %")
        self.gpu_stats.set("driver", gpu.driver or "—")

        self.vram_ring.update(
            gpu.mem_percent, f"{gpu.mem_percent:.0f}%", theme.fmt_bytes(gpu.mem_used)
        )
        self.vram_chart.queue_draw()
        tracked = sum(gpu.processes.values())
        other = max(0.0, gpu.mem_used - tracked)
        self.vram_bar.update([
            ("Processes", tracked, theme.VRAM),
            ("Other", other, "#7a3f86"),
            ("Free", max(0.0, gpu.mem_total - gpu.mem_used), theme.BG_CARD_ALT),
        ], gpu.mem_total)
        self.vram_stats.set("total", theme.fmt_bytes(gpu.mem_total))
        self.vram_stats.set("used", theme.fmt_bytes(gpu.mem_used))
        self.vram_stats.set("free", theme.fmt_bytes(gpu.mem_total - gpu.mem_used))
        self.vram_stats.set("clock", theme.fmt_hz(gpu.clock_mem))

        self._update_clients(s)

    def _update_clients(self, s: Snapshot) -> None:
        procs = s.gpu.processes
        if not procs:
            self.client_empty.set_visible(True)
            self.clients.update([])
            return
        self.client_empty.set_visible(False)
        total = s.gpu.mem_total or 1
        ranked = sorted(procs.items(), key=lambda kv: kv[1], reverse=True)[:12]
        self.clients.update([
            (f"{self.sampler.name_for(pid)}  ·  {pid}", vram / total, theme.fmt_bytes(vram))
            for pid, vram in ranked
        ])
