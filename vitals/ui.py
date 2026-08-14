"""Small composition helpers for building the card-based layout."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import theme  # noqa: E402
from .widgets import Dot, MiniBar  # noqa: E402

ELLIPSIZE_END = 3  # Pango.EllipsizeMode.END, without importing Pango


def width_probe(callback) -> Gtk.Widget:
    """A zero-height child that reports its parent's content width.

    GTK4 hands allocation of Gtk.Box and Gtk.Grid to their layout manager and
    never calls a do_size_allocate override on Python subclasses, so responsive
    containers keep one of these around and listen to it instead.
    """
    area = Gtk.DrawingArea()
    area.set_content_height(0)
    area.set_hexpand(True)
    area.connect("resize", lambda _area, width, _height: callback(width))
    return area


class Card(Gtk.Box):
    """Titled panel with an optional right-aligned header slot."""

    def __init__(self, title: str, accent: theme.Paint | None = None,
                 spacing: int = 8) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        self.add_css_class("card")
        self.set_margin_start(0)
        for setter in (self.set_margin_top, self.set_margin_bottom,
                       self.set_margin_start, self.set_margin_end):
            setter(0)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        self.body.set_margin_top(12)
        self.body.set_margin_bottom(12)
        self.body.set_margin_start(14)
        self.body.set_margin_end(14)
        super().append(self.body)

        self.header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        if accent:
            title_box.append(Dot(accent))
        label = Gtk.Label(label=title.upper(), xalign=0)
        label.add_css_class("card-title")
        title_box.append(label)
        self.header.append(title_box)

        self.header_end = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.header_end.set_halign(Gtk.Align.END)
        self.header_end.set_hexpand(True)
        self.header.append(self.header_end)
        self.body.append(self.header)

    def append(self, child: Gtk.Widget) -> None:  # type: ignore[override]
        self.body.append(child)


def chip(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.add_css_class("chip")
    label.set_valign(Gtk.Align.CENTER)
    return label


def big_value(value: str = "0", unit: str = "%") -> tuple[Gtk.Box, Gtk.Label, Gtk.Label]:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
    box.set_valign(Gtk.Align.END)
    value_label = Gtk.Label(label=value, xalign=0)
    value_label.add_css_class("metric-big")
    unit_label = Gtk.Label(label=unit, xalign=0)
    unit_label.add_css_class("metric-unit")
    unit_label.set_valign(Gtk.Align.BASELINE)
    unit_label.set_margin_bottom(6)
    box.append(value_label)
    box.append(unit_label)
    return box, value_label, unit_label


class StatGrid(Gtk.Box):
    """Label/value grid that drops to fewer columns when space runs out."""

    def __init__(self, columns: int = 2, row_spacing: int = 5,
                 min_cell_width: int = 108) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.grid = Gtk.Grid(row_spacing=row_spacing, column_spacing=10)
        self.append(width_probe(self._on_width))
        self.append(self.grid)
        self.max_columns = columns
        self.min_cell_width = min_cell_width
        self.columns = columns
        self._labels: dict[str, Gtk.Label] = {}
        self._cells: list[Gtk.Widget] = []
        self._pending = False

    def add(self, key: str, caption: str, value: str = "—") -> Gtk.Label:
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        cell.set_hexpand(True)
        caption_label = Gtk.Label(label=caption, xalign=0)
        caption_label.add_css_class("metric-sub")
        # Ellipsizing keeps the grid shrinkable instead of forcing the card wide.
        caption_label.set_ellipsize(ELLIPSIZE_END)
        caption_label.set_tooltip_text(caption)
        value_label = Gtk.Label(label=value, xalign=0)
        value_label.add_css_class("metric-mid")
        value_label.set_ellipsize(ELLIPSIZE_END)
        cell.append(caption_label)
        cell.append(value_label)
        self._cells.append(cell)
        self.grid.attach(cell, (len(self._cells) - 1) % self.columns,
                         (len(self._cells) - 1) // self.columns, 1, 1)
        self._labels[key] = value_label
        return value_label

    def set(self, key: str, value: str) -> None:
        label = self._labels.get(key)
        if label is not None and label.get_label() != value:
            label.set_label(value)

    # -- responsive reflow -----------------------------------------------

    def _columns_for(self, width: int) -> int:
        if width <= 1:
            return self.columns
        fits = (width + 10) // (self.min_cell_width + 10)
        return max(1, min(self.max_columns, int(fits)))

    def _reflow(self, columns: int) -> bool:
        self._pending = False
        if columns == self.columns:
            return False
        self.columns = columns
        for cell in self._cells:
            self.grid.remove(cell)
        for index, cell in enumerate(self._cells):
            self.grid.attach(cell, index % columns, index // columns, 1, 1)
        return False

    def _on_width(self, width: int) -> None:
        columns = self._columns_for(width)
        if columns != self.columns and not self._pending:
            self._pending = True
            # Never restructure from inside an allocation.
            GLib.idle_add(self._reflow, columns, priority=GLib.PRIORITY_HIGH_IDLE)


class ReflowGrid(Gtk.Box):
    """Equal-width children in as many columns as the current width allows.

    Cards keep their proportions in a portrait window by stacking, and spread
    back out into a grid when the window is widened.
    """

    def __init__(self, min_child_width: int = 320, spacing: int = 12,
                 choices: tuple[int, ...] | None = None) -> None:
        # Row gaps come from margins, not box spacing, so that the width probe
        # does not leave a gap of its own.
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.min_child_width = min_child_width
        self.choices = choices
        self._spacing = spacing
        self._children: list[Gtk.Widget] = []
        self._rows: list[Gtk.Box] = []
        self._columns = 1
        self._pending = False
        self.append(width_probe(self._on_width))

    def add(self, child: Gtk.Widget) -> None:
        self._children.append(child)
        self._build(self._columns)

    def _columns_for(self, width: int) -> int:
        if width <= 1:
            return self._columns
        options = sorted(self.choices, reverse=True) if self.choices \
            else range(len(self._children), 0, -1)
        for count in options:
            if width >= count * self.min_child_width + (count - 1) * self._spacing:
                return min(count, max(1, len(self._children)))
        return 1

    def _build(self, columns: int) -> None:
        self._columns = columns
        for child in self._children:
            parent = child.get_parent()
            if parent is not None:
                parent.remove(child)
        for row in self._rows:
            self.remove(row)
        self._rows.clear()

        row: Gtk.Box | None = None
        for index, child in enumerate(self._children):
            if index % columns == 0:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                              spacing=self._spacing, homogeneous=True)
                if self._rows:
                    row.set_margin_top(self._spacing)
                self._rows.append(row)
                self.append(row)
            row.append(child)
        # Pad the last row so a stray child does not stretch across the grid.
        if row is not None:
            for _ in range(-len(self._children) % columns):
                row.append(Gtk.Box())

    def _reflow(self, columns: int) -> bool:
        self._pending = False
        if columns != self._columns:
            self._build(columns)
        return False

    def _on_width(self, width: int) -> None:
        columns = self._columns_for(width)
        if columns != self._columns and not self._pending:
            self._pending = True
            # Never restructure from inside an allocation.
            GLib.idle_add(self._reflow, columns, priority=GLib.PRIORITY_HIGH_IDLE)


class TopList(Gtk.Box):
    """Fixed set of reused rows showing 'name — bar — value' entries."""

    def __init__(self, color: theme.Paint, rows: int = 6, name_width: int = 148,
                 value_width: int = 84) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._rows: list[tuple[Gtk.Box, Gtk.Label, MiniBar, Gtk.Label]] = []
        for _ in range(rows):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            name = Gtk.Label(xalign=0)
            name.add_css_class("metric-sub")
            name.set_size_request(name_width, -1)
            name.set_ellipsize(ELLIPSIZE_END)
            bar = MiniBar(color, height=7)
            value = Gtk.Label(xalign=1)
            value.add_css_class("metric-sub")
            value.set_size_request(value_width, -1)
            row.append(name)
            row.append(bar)
            row.append(value)
            self.append(row)
            self._rows.append((row, name, bar, value))

    def update(self, entries: list[tuple[str, float, str]]) -> None:
        for i, (row, name, bar, value) in enumerate(self._rows):
            if i < len(entries):
                label, fraction, text = entries[i]
                row.set_visible(True)
                name.set_label(label)
                bar.update(fraction)
                value.set_label(text)
            else:
                row.set_visible(False)


def scroll(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.set_vexpand(True)
    sw.set_child(child)
    return sw


def page_box(spacing: int = 12, margin: int = 14) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    box.set_margin_top(margin)
    box.set_margin_bottom(margin)
    box.set_margin_start(margin)
    box.set_margin_end(margin)
    return box
