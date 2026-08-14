"""Custom chart widgets rendered with GSK snapshots."""

from __future__ import annotations

import math
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gsk, Gtk  # noqa: E402

from . import draw, theme  # noqa: E402
from .sampler import Series  # noqa: E402


def nice_ceiling(value: float) -> float:
    """Round up to a visually tidy axis maximum."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    base = 10 ** exponent
    for step in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if value <= step * base:
            return step * base
    return 10 * base


@dataclass
class Trace:
    series: Series
    color: theme.Paint
    label: str = ""
    fill: bool = True


def plot_points(
    values: list[float], window: int, width: float, y_of
) -> list[tuple[float, float]]:
    """Map samples to coordinates, newest pinned to the right edge.

    A partially filled history therefore grows in from the right rather than
    pretending the missing samples were zero.
    """
    step = width / max(1, window - 1)
    visible = values[-window:]
    if len(visible) < 2:
        return []
    origin = width - (len(visible) - 1) * step
    return [(origin + i * step, y_of(value)) for i, value in enumerate(visible)]


def line_path(points: list[tuple[float, float]]) -> Gsk.Path:
    builder = Gsk.PathBuilder.new()
    for i, (x, y) in enumerate(points):
        builder.line_to(x, y) if i else builder.move_to(x, y)
    return builder.to_path()


def area_path(points: list[tuple[float, float]], baseline: float) -> Gsk.Path:
    builder = Gsk.PathBuilder.new()
    builder.move_to(points[0][0], baseline)
    for x, y in points:
        builder.line_to(x, y)
    builder.line_to(points[-1][0], baseline)
    builder.close()
    return builder.to_path()


class Chart(Gtk.Widget):
    """Time-series area chart. Percent mode pins the axis to 0-100."""

    def __init__(
        self,
        traces: list[Trace] | None = None,
        percent: bool = True,
        height: int = 90,
        formatter=None,
        show_axis: bool = True,
        line_width: float = 1.6,
        grid_rows: int = 4,
        window: int | None = None,
    ) -> None:
        super().__init__()
        self.traces = traces or []
        self.percent = percent
        self.formatter = formatter or (lambda v: f"{v:.0f}%")
        self.show_axis = show_axis
        self.line_width = line_width
        self.grid_rows = grid_rows
        self.window = window
        self.baseline_max = 1.0
        self._height = height
        self.set_hexpand(True)

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self._height, self._height, -1, -1)
        return (120, 240, -1, -1)

    def _axis_max(self) -> float:
        if self.percent:
            return 100.0
        peak = max((t.series.peak() for t in self.traces), default=0.0)
        return nice_ceiling(max(peak, self.baseline_max))

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width = self.get_width()
        height = self.get_height()
        if width <= 1 or height <= 1:
            return
        pad_right = 46 if self.show_axis else 2
        plot_w = max(1.0, width - pad_right)
        top = 1.0
        plot_h = max(1.0, height - 2)
        axis_max = self._axis_max()

        for i in range(self.grid_rows + 1):
            y = top + plot_h * i / self.grid_rows
            edge = i in (0, self.grid_rows)
            draw.fill_rect(snapshot, 0, round(y), plot_w, 1, theme.GRID,
                           0.9 if edge else 0.5)
        for i in range(1, 6):
            x = plot_w * i / 6
            draw.fill_rect(snapshot, round(x), top, 1, plot_h, theme.GRID, 0.3)

        if self.show_axis:
            draw.text(self, snapshot, width - 3, top - 2, self.formatter(axis_max),
                      size=9, color=theme.TEXT_FAINT, align="right")
            draw.text(self, snapshot, width - 3, top + plot_h - 12, self.formatter(0),
                      size=9, color=theme.TEXT_FAINT, align="right")

        area = draw.rect(0, top, plot_w, plot_h)
        baseline = top + plot_h

        def y_of(value: float) -> float:
            ratio = min(1.0, value / axis_max) if axis_max else 0.0
            return baseline - ratio * plot_h

        for trace in self.traces:
            window = self.window or trace.series.maxlen
            points = plot_points(trace.series.real_values(), window, plot_w, y_of)
            if not points:
                continue
            if trace.fill:
                draw.gradient_fill(snapshot, area_path(points, baseline), area,
                                   trace.color)
            draw.stroke(snapshot, line_path(points), self.line_width,
                        trace.color, 0.95)
            draw.fill_circle(snapshot, points[-1][0], points[-1][1], 2.4, trace.color)


class CoreTile(Gtk.Widget):
    """One logical CPU: label, percentage, mini history graph and load bar."""

    HEIGHT = 66
    WINDOW = 45

    def __init__(self, index: int, series: Series) -> None:
        super().__init__()
        self.index = index
        self.series = series
        self.freq = 0.0
        self.set_hexpand(True)

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self.HEIGHT, self.HEIGHT, -1, -1)
        return (120, 200, -1, -1)

    def update(self, freq: float) -> None:
        self.freq = freq
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 1:
            return
        value = self.series.latest
        color = theme.core_color(self.index)

        label_w, _ = draw.text(self, snapshot, 0, 0, f"CPU {self.index}", size=10,
                               color=theme.TEXT_DIM, bold=True)
        draw.text(self, snapshot, width, -2, f"{value:.0f}%", size=12,
                  color=theme.heat_color(value), bold=True, align="right")
        if self.freq and width > 150:
            draw.text(self, snapshot, label_w + 8, 1, theme.fmt_hz(self.freq),
                      size=9, color=theme.TEXT_FAINT)

        graph_top = 19.0
        graph_h = max(1.0, height - graph_top - 10)
        baseline = graph_top + graph_h

        def y_of(sample: float) -> float:
            return baseline - min(1.0, sample / 100) * graph_h

        points = plot_points(self.series.real_values(), self.WINDOW, width, y_of)
        if points:
            draw.gradient_fill(
                snapshot, area_path(points, baseline),
                draw.rect(0, graph_top, width, graph_h), color, 0.32, 0.02,
            )
            draw.stroke(snapshot, line_path(points), 1.3, color, 0.9)

        bar_y = height - 5.0
        draw.fill_rounded(snapshot, 0, bar_y, width, 4, 2, theme.BG_CARD_ALT)
        fill_w = max(0.0, min(1.0, value / 100)) * width
        if fill_w > 0.8:
            draw.fill_rounded(snapshot, 0, bar_y, fill_w, 4, 2,
                              theme.heat_color(value))


class Ring(Gtk.Widget):
    """Donut gauge with a centered value and caption."""

    def __init__(self, color: theme.Paint, size: int = 116, caption: str = "") -> None:
        super().__init__()
        self.color = color
        self.value = 0.0
        self.caption = caption
        self.center_text = "0%"
        self.sub_text = ""
        self._size = size
        self._caption_h = 18 if caption else 0

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int):
        if orientation == Gtk.Orientation.VERTICAL:
            height = self._size + self._caption_h
            return (height, height, -1, -1)
        return (self._size, self._size, -1, -1)

    def update(self, percent: float, center_text: str, sub_text: str = "") -> None:
        self.value = max(0.0, min(100.0, percent))
        self.center_text = center_text
        self.sub_text = sub_text
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 1:
            return
        ring_h = height - self._caption_h
        cx, cy = width / 2, ring_h / 2
        thickness = 9.0
        radius = min(width, ring_h) / 2 - thickness / 2 - 2
        if radius <= 1:
            return

        draw.stroke(snapshot, draw.arc_path(cx, cy, radius, 0, 360), thickness,
                    theme.BG_CARD_ALT)
        if self.value > 0.4:
            draw.stroke(
                snapshot,
                draw.arc_path(cx, cy, radius, -90, 360 * self.value / 100),
                thickness, self.color,
            )

        draw.text(self, snapshot, cx, cy - 16, self.center_text, size=19,
                  color=theme.TEXT, align="center")
        if self.sub_text:
            draw.text(self, snapshot, cx, cy + 7, self.sub_text, size=10,
                      color=theme.TEXT_DIM, align="center")
        if self.caption:
            draw.text(self, snapshot, cx, ring_h + 3, self.caption, size=9,
                      color=theme.TEXT_FAINT, align="center", bold=True)


class SegmentBar(Gtk.Widget):
    """Horizontal stacked bar with an inline legend."""

    def __init__(self, height: int = 34) -> None:
        super().__init__()
        self.segments: list[tuple[str, float, theme.Paint]] = []
        self.total = 1.0
        self._height = height
        self.set_hexpand(True)

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self._height, self._height, -1, -1)
        return (140, 260, -1, -1)

    def update(self, segments: list[tuple[str, float, theme.Paint]], total: float) -> None:
        self.segments = segments
        self.total = max(1.0, total)
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width = self.get_width()
        if width <= 1:
            return
        bar_h = 13.0
        snapshot.push_rounded_clip(draw.rounded_rect(0, 0, width, bar_h, 4.0))
        draw.fill_rect(snapshot, 0, 0, width, bar_h, theme.BG_CARD_ALT)
        x = 0.0
        for _label, value, color in self.segments:
            seg_w = width * max(0.0, value) / self.total
            if seg_w > 0.4:
                draw.fill_rect(snapshot, x, 0, seg_w, bar_h, color)
            x += seg_w
        snapshot.pop()

        legend_x = 0.0
        for label, value, color in self.segments:
            draw.fill_rounded(snapshot, legend_x, bar_h + 10, 7, 7, 2, color)
            w1, _ = draw.text(self, snapshot, legend_x + 11, bar_h + 8, label,
                              size=10, color=theme.TEXT_DIM)
            w2, _ = draw.text(self, snapshot, legend_x + 15 + w1, bar_h + 8,
                              theme.fmt_bytes(value), size=10, color=theme.TEXT)
            legend_x += 26 + w1 + w2
            if legend_x > width - 50:
                break


class Bars(Gtk.Widget):
    """Compact vertical bar readout, one bar per value (e.g. every core)."""

    MAX_BAR_WIDTH = 72.0

    def __init__(self, height: int = 58) -> None:
        super().__init__()
        self.values: list[float] = []
        self._height = height
        self.set_hexpand(True)

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self._height, self._height, -1, -1)
        return (140, 300, -1, -1)

    def update(self, values: list[float]) -> None:
        self.values = values
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if not self.values or width <= 1:
            return
        count = len(self.values)
        gap = 6.0 if count <= 16 else (3.0 if count <= 32 else 1.5)
        bar_w = max(2.0, (width - gap * (count - 1)) / count)
        # Few cores across a wide card would give unreadably chunky bars.
        bar_w = min(bar_w, self.MAX_BAR_WIDTH)
        span = bar_w * count + gap * (count - 1)
        left = (width - span) / 2
        label_h = 13.0
        plot_h = max(1.0, height - label_h)

        for i, value in enumerate(self.values):
            x = left + i * (bar_w + gap)
            draw.fill_rounded(snapshot, x, 0, bar_w, plot_h, 4, theme.BG_CARD_ALT)
            fill_h = max(0.0, min(1.0, value / 100)) * plot_h
            if fill_h > 1:
                draw.fill_rounded(snapshot, x, plot_h - fill_h, bar_w, fill_h, 4,
                                  theme.core_color(i))
            if bar_w >= 18:
                draw.text(self, snapshot, x + bar_w / 2, plot_h + 1,
                          f"{value:.0f}", size=9, color=theme.TEXT_FAINT,
                          align="center")


class Dot(Gtk.Widget):
    """Small accent dot used in card headers."""

    def __init__(self, color: theme.Paint, size: int = 8) -> None:
        super().__init__()
        self.color = color
        self._size = size
        self.set_valign(Gtk.Align.CENTER)

    def do_measure(self, _orientation: Gtk.Orientation, _for_size: int):
        return (self._size, self._size, -1, -1)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        size = min(self.get_width(), self.get_height())
        if size <= 0:
            return
        draw.fill_circle(snapshot, self.get_width() / 2, self.get_height() / 2,
                         size / 2, self.color)


class MiniBar(Gtk.Widget):
    """Thin proportional bar used in list rows."""

    def __init__(self, color: theme.Paint, height: int = 6) -> None:
        super().__init__()
        self.color = color
        self.fraction = 0.0
        self._height = height
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self._height, self._height, -1, -1)
        return (60, 200, -1, -1)

    def update(self, fraction: float) -> None:
        self.fraction = max(0.0, min(1.0, fraction))
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 1:
            return
        radius = height / 2
        draw.fill_rounded(snapshot, 0, 0, width, height, radius, theme.BG_CARD_ALT)
        fill = width * self.fraction
        if fill > 1:
            draw.fill_rounded(snapshot, 0, 0, fill, height, radius, self.color)
