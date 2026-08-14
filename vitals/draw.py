"""Drawing primitives built on GSK.

Rendering goes through Gtk.Snapshot rather than cairo: it is GPU-accelerated
and, unlike cairo, needs no PyGObject foreign-struct bridge at runtime.
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import theme  # noqa: E402

_RGBA_CACHE: dict[tuple[str, float], Gdk.RGBA] = {}


def rgba(color: theme.Paint, alpha: float = 1.0) -> Gdk.RGBA:
    # Keyed by the resolved value, so a palette token picks up a new entry after
    # a theme switch instead of being served the previous theme's color.
    key = (str(color), round(alpha, 3))
    cached = _RGBA_CACHE.get(key)
    if cached is None:
        r, g, b = theme.rgb(key[0])
        cached = Gdk.RGBA(red=r, green=g, blue=b, alpha=alpha)
        _RGBA_CACHE[key] = cached
    return cached


def rect(x: float, y: float, w: float, h: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, max(0.0, w), max(0.0, h))


def fill_rect(
    snapshot: Gtk.Snapshot, x: float, y: float, w: float, h: float,
    color: theme.Paint, alpha: float = 1.0,
) -> None:
    if w <= 0 or h <= 0:
        return
    snapshot.append_color(rgba(color, alpha), rect(x, y, w, h))


def rounded_rect(
    x: float, y: float, w: float, h: float, radius: float
) -> Gsk.RoundedRect:
    """Build a GskRoundedRect.

    The struct must be mutated in place and returned by name: passing the value
    returned by ``init_from_rect`` on a temporary loses the underlying memory.
    """
    rounded = Gsk.RoundedRect()
    rounded.init_from_rect(rect(x, y, w, h), max(0.0, min(radius, w / 2, h / 2)))
    return rounded


def fill_rounded(
    snapshot: Gtk.Snapshot, x: float, y: float, w: float, h: float,
    radius: float, color: theme.Paint, alpha: float = 1.0,
) -> None:
    if w <= 0 or h <= 0:
        return
    snapshot.push_rounded_clip(rounded_rect(x, y, w, h, radius))
    snapshot.append_color(rgba(color, alpha), rect(x, y, w, h))
    snapshot.pop()


def fill_circle(
    snapshot: Gtk.Snapshot, cx: float, cy: float, radius: float,
    color: theme.Paint, alpha: float = 1.0,
) -> None:
    fill_rounded(snapshot, cx - radius, cy - radius, radius * 2, radius * 2,
                 radius, color, alpha)


def stroke(
    snapshot: Gtk.Snapshot, path: Gsk.Path, width: float,
    color: theme.Paint, alpha: float = 1.0, round_ends: bool = True,
) -> None:
    line = Gsk.Stroke.new(width)
    if round_ends:
        line.set_line_cap(Gsk.LineCap.ROUND)
        line.set_line_join(Gsk.LineJoin.ROUND)
    snapshot.append_stroke(path, line, rgba(color, alpha))


def stroke_line(
    snapshot: Gtk.Snapshot, x1: float, y1: float, x2: float, y2: float,
    width: float, color: theme.Paint, alpha: float = 1.0,
) -> None:
    builder = Gsk.PathBuilder.new()
    builder.move_to(x1, y1)
    builder.line_to(x2, y2)
    stroke(snapshot, builder.to_path(), width, color, alpha, round_ends=False)


def gradient_fill(
    snapshot: Gtk.Snapshot, path: Gsk.Path, area: Graphene.Rect,
    color: theme.Paint, top_alpha: float = 0.42, bottom_alpha: float = 0.02,
) -> None:
    """Fill a path with a vertical fade of a single hue."""
    snapshot.push_fill(path, Gsk.FillRule.WINDING)
    top = Gsk.ColorStop()
    top.offset = 0.0
    top.color = rgba(color, top_alpha)
    bottom = Gsk.ColorStop()
    bottom.offset = 1.0
    bottom.color = rgba(color, bottom_alpha)
    snapshot.append_linear_gradient(
        area,
        Graphene.Point().init(area.get_x(), area.get_y()),
        Graphene.Point().init(area.get_x(), area.get_y() + area.get_height()),
        [top, bottom],
    )
    snapshot.pop()


def arc_path(
    cx: float, cy: float, radius: float, start_deg: float, sweep_deg: float
) -> Gsk.Path:
    """Arc approximated by a polyline; ~3 degrees per segment reads as smooth."""
    sweep_deg = max(-360.0, min(360.0, sweep_deg))
    steps = max(2, int(abs(sweep_deg) / 3) + 1)
    builder = Gsk.PathBuilder.new()
    for i in range(steps + 1):
        angle = math.radians(start_deg + sweep_deg * i / steps)
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        if i:
            builder.line_to(x, y)
        else:
            builder.move_to(x, y)
    return builder.to_path()


_FONT_CACHE: dict[tuple[str, float, bool], Pango.FontDescription] = {}


def _font(size: float, bold: bool, mono: bool) -> Pango.FontDescription:
    key = ("mono" if mono else "sans", size, bold)
    desc = _FONT_CACHE.get(key)
    if desc is None:
        desc = Pango.FontDescription()
        desc.set_family("monospace" if mono else "sans")
        desc.set_absolute_size(size * Pango.SCALE)
        desc.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
        _FONT_CACHE[key] = desc
    return desc


def text(
    widget: Gtk.Widget,
    snapshot: Gtk.Snapshot,
    x: float,
    y: float,
    label: str,
    size: float = 10,
    color: theme.Paint = theme.TEXT,
    bold: bool = False,
    align: str = "left",
    alpha: float = 1.0,
    mono: bool = False,
) -> tuple[int, int]:
    layout = widget.create_pango_layout(label)
    layout.set_font_description(_font(size, bold, mono))
    width, height = layout.get_pixel_size()
    if align == "right":
        x -= width
    elif align == "center":
        x -= width / 2
    snapshot.save()
    snapshot.translate(Graphene.Point().init(x, y))
    snapshot.append_layout(layout, rgba(color, alpha))
    snapshot.restore()
    return width, height
