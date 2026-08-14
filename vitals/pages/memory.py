"""Memory and swap detail page."""

from __future__ import annotations

import gi
import psutil

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .. import theme, ui  # noqa: E402
from ..sampler import Sampler, Snapshot  # noqa: E402
from ..widgets import Chart, MiniBar, Ring, SegmentBar, Trace  # noqa: E402


class MemoryPage(Gtk.Box):
    def __init__(self, sampler: Sampler) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        root = ui.page_box()

        mem = ui.Card("Physical Memory", accent=theme.MEM)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.mem_ring = Ring(theme.MEM, size=132, caption="IN USE")
        self.mem_ring.set_valign(Gtk.Align.CENTER)
        top.append(self.mem_ring)
        self.mem_chart = Chart(
            [Trace(sampler.hist_mem, theme.MEM)], percent=True, height=132
        )
        top.append(self.mem_chart)
        mem.append(top)
        self.mem_bar = SegmentBar(height=40)
        mem.append(self.mem_bar)
        self.mem_stats = ui.StatGrid(columns=4)
        for key, caption in (
            ("total", "Total"), ("used", "Used"),
            ("available", "Available"), ("cached", "Cached + buffers"),
        ):
            self.mem_stats.add(key, caption)
        self.mem_stats.set_margin_top(6)
        mem.append(self.mem_stats)
        root.append(mem)

        swap = ui.Card("Swap", accent=theme.SWAP)
        top2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.swap_ring = Ring(theme.SWAP, size=132, caption="IN USE")
        self.swap_ring.set_valign(Gtk.Align.CENTER)
        top2.append(self.swap_ring)
        self.swap_chart = Chart(
            [Trace(sampler.hist_swap, theme.SWAP)], percent=True, height=132
        )
        top2.append(self.swap_chart)
        swap.append(top2)
        self.swap_stats = ui.StatGrid(columns=4)
        for key, caption in (
            ("total", "Total"), ("used", "Used"),
            ("sin", "Swap in /s"), ("sout", "Swap out /s"),
        ):
            self.swap_stats.add(key, caption)
        self.swap_stats.set_margin_top(6)
        swap.append(self.swap_stats)
        root.append(swap)

        disks = ui.Card("Mounted Filesystems", accent=theme.DISK)
        self.disk_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        disks.append(self.disk_box)
        self._disk_rows: dict[str, tuple[Gtk.Label, MiniBar, Gtk.Label]] = {}
        root.append(disks)

        self.append(ui.scroll(root))

    @staticmethod
    def _is_interesting(part) -> bool:
        # Snap/appimage loop mounts are read-only and always report 100% full.
        if part.fstype in ("squashfs", "erofs", "iso9660"):
            return False
        return not part.mountpoint.startswith(("/snap", "/var/snap", "/var/lib/snapd"))

    def _ensure_disk_rows(self) -> None:
        try:
            partitions = psutil.disk_partitions(all=False)
        except OSError:
            return
        for part in partitions:
            if part.mountpoint in self._disk_rows or not self._is_interesting(part):
                continue
            try:
                psutil.disk_usage(part.mountpoint)
            except (OSError, PermissionError):
                continue
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = Gtk.Label(label=part.mountpoint, xalign=0)
            name.add_css_class("metric-sub")
            value = Gtk.Label(label="", xalign=1)
            value.add_css_class("metric-sub")
            value.set_hexpand(True)
            head.append(name)
            head.append(value)
            bar = MiniBar(theme.DISK, height=7)
            row.append(head)
            row.append(bar)
            self.disk_box.append(row)
            self._disk_rows[part.mountpoint] = (name, bar, value)

    def update(self, s: Snapshot) -> None:
        self.mem_ring.update(
            s.mem_percent, f"{s.mem_percent:.0f}%", theme.fmt_bytes(s.mem_used)
        )
        self.mem_chart.queue_draw()
        app_mem = max(0.0, s.mem_used - s.mem_cached - s.mem_buffers)
        self.mem_bar.update([
            ("Apps", app_mem, theme.MEM),
            ("Cache", s.mem_cached + s.mem_buffers, theme.MEM_CACHE),
            ("Free", s.mem_available, theme.BG_CARD_ALT),
        ], s.mem_total)
        self.mem_stats.set("total", theme.fmt_bytes(s.mem_total))
        self.mem_stats.set("used", f"{theme.fmt_bytes(s.mem_used)} · {s.mem_percent:.1f}%")
        self.mem_stats.set("available", theme.fmt_bytes(s.mem_available))
        self.mem_stats.set("cached", theme.fmt_bytes(s.mem_cached + s.mem_buffers))

        if s.swap_total:
            self.swap_ring.update(
                s.swap_percent, f"{s.swap_percent:.0f}%", theme.fmt_bytes(s.swap_used)
            )
        else:
            self.swap_ring.update(0, "off", "no swap")
        self.swap_chart.queue_draw()
        self.swap_stats.set("total", theme.fmt_bytes(s.swap_total) if s.swap_total else "—")
        self.swap_stats.set("used", theme.fmt_bytes(s.swap_used))
        self.swap_stats.set("sin", theme.fmt_rate(s.swap_in_rate))
        self.swap_stats.set("sout", theme.fmt_rate(s.swap_out_rate))

        self._ensure_disk_rows()
        for mount, (_name, bar, value) in self._disk_rows.items():
            try:
                usage = psutil.disk_usage(mount)
            except OSError:
                continue
            bar.update(usage.percent / 100)
            value.set_label(
                f"{theme.fmt_bytes(usage.used)} / {theme.fmt_bytes(usage.total)}"
                f"  ({usage.percent:.0f}%)"
            )
