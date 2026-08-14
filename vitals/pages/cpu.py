"""Per-core CPU detail page."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .. import theme, ui  # noqa: E402
from ..sampler import Sampler, Snapshot  # noqa: E402
from ..widgets import Chart, CoreTile, Trace  # noqa: E402


class CpuPage(Gtk.Box):
    def __init__(self, sampler: Sampler) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.sampler = sampler
        root = ui.page_box()

        total = ui.Card("Total CPU Load", accent=theme.CPU)
        self.chip_model = ui.chip(_cpu_model())
        total.header_end.append(self.chip_model)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.set_valign(Gtk.Align.CENTER)
        left.set_size_request(104, -1)
        box, self.total_value, _ = ui.big_value("0", "%")
        left.append(box)
        caption = Gtk.Label(
            label=f"{sampler.physical_cores} cores / {sampler.core_count} threads",
            xalign=0,
        )
        caption.add_css_class("metric-sub")
        caption.set_wrap(True)
        left.append(caption)
        row.append(left)
        self.total_chart = Chart(
            [Trace(sampler.hist_cpu, theme.CPU)], percent=True, height=132
        )
        row.append(self.total_chart)
        total.append(row)

        self.stats = ui.StatGrid(columns=4)
        self.stats.add("freq", "Avg frequency")
        self.stats.add("temp", "Temperature")
        self.stats.add("load", "Load average")
        self.stats.add("ctx", "Context switches/s")
        self.stats.set_margin_top(6)
        total.append(self.stats)
        root.append(total)

        cores = ui.Card("Logical Processors", accent=theme.CPU)
        self.core_grid = ui.ReflowGrid(min_child_width=132, spacing=14)
        self.tiles: list[CoreTile] = []
        for i in range(sampler.core_count):
            tile = CoreTile(i, sampler.hist_cores[i])
            self.tiles.append(tile)
            self.core_grid.add(tile)
        cores.append(self.core_grid)
        root.append(cores)

        top = ui.Card("Top CPU Consumers", accent=theme.CPU)
        self.top_list = ui.TopList(theme.CPU, rows=6)
        top.append(self.top_list)
        root.append(top)

        self.append(ui.scroll(root))

    def update(self, s: Snapshot) -> None:
        self.total_value.set_label(f"{s.cpu_total:.1f}")
        self.total_chart.queue_draw()
        self.stats.set("freq", theme.fmt_hz(s.cpu_freq_avg) if s.cpu_freq_avg else "n/a")
        self.stats.set("temp", f"{s.cpu_temp:.0f} °C" if s.cpu_temp else "n/a")
        self.stats.set(
            "load", f"{s.load_avg[0]:.2f} {s.load_avg[1]:.2f} {s.load_avg[2]:.2f}"
        )
        self.stats.set("ctx", f"{s.ctx_switches:,.0f}")
        for i, tile in enumerate(self.tiles):
            freq = s.cpu_freqs[i] if i < len(s.cpu_freqs) else (
                s.cpu_freqs[0] if s.cpu_freqs else 0.0
            )
            tile.update(freq)

        # A process can exceed 100% by using several cores, so scale the bars
        # against the busiest entry rather than a single core.
        top = sorted(s.processes, key=lambda row: row.cpu, reverse=True)[:6]
        scale = max(100.0, top[0].cpu if top else 0.0)
        self.top_list.update([
            (f"{row.name}  ·  {row.pid}", row.cpu / scale, f"{row.cpu:.1f} %")
            for row in top
        ])


def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "Unknown CPU"
