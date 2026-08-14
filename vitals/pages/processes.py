"""Sortable, searchable, live process table.

The rows are bound positionally: filtering and sorting happen in Python and
row N of the model always holds the Nth process of the current ordering. Asking
GtkColumnView to re-sort a model of ~2000 rows on every tick does not reorder
the rendered rows reliably, so the model order is kept fixed instead and only
the values inside each row change.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import sampler as sampler_mod  # noqa: E402
from .. import theme, ui  # noqa: E402
from ..sampler import ProcRow, Sampler, Snapshot  # noqa: E402

# title, ProcRow attribute, formatter, width, numeric, expand, keep-order.
# The last field ranks the columns by usefulness: in a narrow window only the
# ones that fit are shown, dropped from the least useful up.
COLUMNS = [
    ("PID", "pid", lambda r: str(r.pid), 78, True, False, 4),
    ("Name", "name", lambda r: r.name, 176, False, True, 1),
    ("User", "user", lambda r: r.user, 104, False, False, 6),
    ("CPU %", "cpu", lambda r: f"{r.cpu:.1f}", 70, True, False, 2),
    ("Memory", "rss", lambda r: theme.fmt_bytes(r.rss), 88, True, False, 3),
    ("Mem %", "mem_percent", lambda r: f"{r.mem_percent:.1f}", 68, True, False, 5),
    ("VRAM", "vram",
     lambda r: theme.fmt_bytes(r.vram) if r.vram else "—", 84, True, False, 7),
    ("Threads", "threads", lambda r: str(r.threads), 70, True, False, 8),
    ("Status", "status", lambda r: r.status, 88, False, False, 9),
]

# Room taken by the card border and the vertical scrollbar.
TABLE_CHROME = 22


class ProcObject(GObject.Object):
    """Model row holding one ProcRow.

    The payload is a plain attribute rather than GObject properties: nothing
    binds to it, and emitting thousands of notify signals per tick is pure cost.
    """

    __gtype_name__ = "VitalsProcObject"

    def __init__(self) -> None:
        super().__init__()
        self.row = ProcRow(pid=0, name="", user="", cpu=0.0, mem_percent=0.0,
                           rss=0.0, threads=0, status="")


class ProcessPage(Gtk.Box):
    def __init__(self, sampler: Sampler, toast_overlay: Adw.ToastOverlay) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.sampler = sampler
        self.toast_overlay = toast_overlay
        self._own_pid = sampler_mod.own_pid()
        self._renderers: dict[Gtk.ListItem, object] = {}
        self._column_keys: dict[Gtk.ColumnViewColumn, str] = {}
        self._column_thresholds: dict[Gtk.ColumnViewColumn, int] = {}
        self._fit_pending = False
        self._sort_key = "cpu"
        self._sort_descending = True
        self._selected_pid: int | None = None
        self._restoring_selection = False
        self._rows: list[ProcRow] = []
        self._latest: Snapshot | None = None

        self.store = Gio.ListStore(item_type=ProcObject)
        self.column_view = Gtk.ColumnView()
        self.column_view.set_show_row_separators(True)
        self.column_view.set_show_column_separators(False)
        self.column_view.set_vexpand(True)

        self.selection = Gtk.SingleSelection(model=self.store)
        self.selection.set_autoselect(False)
        self.selection.set_can_unselect(True)
        self.selection.connect("notify::selected", self._on_selection_changed)
        self.column_view.set_model(self.selection)

        self._build_columns()
        self.column_view.get_sorter().connect("changed", self._on_sort_changed)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for setter in (root.set_margin_top, root.set_margin_bottom,
                       root.set_margin_start, root.set_margin_end):
            setter(14)
        root.append(self._build_toolbar())

        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("card")
        frame.set_vexpand(True)
        frame.append(ui.width_probe(self._on_width))
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self.column_view)
        frame.append(scroller)
        root.append(frame)
        self.append(root)

    # -- toolbar ---------------------------------------------------------

    def _build_toolbar(self) -> Gtk.Widget:
        # Two rows: a portrait window has no room for filter and actions side
        # by side.
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Filter by name, user or PID")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", lambda *_: self._reapply())
        top.append(self.search)

        self.kernel_check = Gtk.CheckButton(label="Kernel threads")
        self.kernel_check.add_css_class("metric-sub")
        self.kernel_check.set_tooltip_text(
            "Include kernel threads. They are always idle and add hundreds of rows."
        )
        self.kernel_check.connect(
            "toggled",
            lambda button: setattr(self.sampler, "include_kernel", button.get_active()),
        )
        top.append(self.kernel_check)
        bar.append(top)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.summary = Gtk.Label(label="", xalign=0)
        self.summary.add_css_class("metric-sub")
        self.summary.set_hexpand(True)
        self.summary.set_ellipsize(ui.ELLIPSIZE_END)
        bottom.append(self.summary)

        end_button = Gtk.Button(label="End")
        end_button.set_tooltip_text("Send SIGTERM to the selected process")
        end_button.connect("clicked", lambda *_: self._end_selected(force=False))
        bottom.append(end_button)

        kill_button = Gtk.Button(label="Kill")
        kill_button.set_tooltip_text("Send SIGKILL to the selected process")
        kill_button.add_css_class("destructive-action")
        kill_button.connect("clicked", lambda *_: self._end_selected(force=True))
        bottom.append(kill_button)
        bar.append(bottom)
        return bar

    # -- columns ---------------------------------------------------------

    def _build_columns(self) -> None:
        budget = 0
        thresholds: dict[str, int] = {}
        for spec in sorted(COLUMNS, key=lambda c: c[6]):
            budget += spec[3]
            thresholds[spec[1]] = budget

        for title, key, formatter, width, numeric, expand, _rank in COLUMNS:
            factory = Gtk.SignalListItemFactory()
            colorize = key == "cpu"

            def on_setup(_factory, list_item: Gtk.ListItem, numeric=numeric) -> None:
                label = Gtk.Label(xalign=1.0 if numeric else 0.0)
                label.set_halign(Gtk.Align.END if numeric else Gtk.Align.START)
                label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
                label.add_css_class("cell-num" if numeric else "cell-text")
                label.set_margin_start(8)
                label.set_margin_end(8)
                list_item.set_child(label)

            def render(list_item: Gtk.ListItem, formatter=formatter,
                       colorize=colorize) -> None:
                item = list_item.get_item()
                label = list_item.get_child()
                if item is None or label is None:
                    return
                row = item.row
                text = formatter(row)
                if colorize and row.cpu >= 1:
                    color = theme.heat_color(min(100.0, row.cpu))
                    label.set_markup(f'<span foreground="{color}">{text}</span>')
                else:
                    label.set_text(text)
                label.set_opacity(0.6 if row.pid == self._own_pid else 1.0)

            def on_bind(_factory, list_item: Gtk.ListItem, render=render) -> None:
                self._renderers[list_item] = render
                render(list_item)

            def on_unbind(_factory, list_item: Gtk.ListItem) -> None:
                self._renderers.pop(list_item, None)

            factory.connect("setup", on_setup)
            factory.connect("bind", on_bind)
            factory.connect("unbind", on_unbind)

            column = Gtk.ColumnViewColumn(title=title, factory=factory)
            # The sorter is only here to make the header clickable and show the
            # arrow; the actual ordering is done in Python.
            column.set_sorter(Gtk.CustomSorter.new(lambda *_: 0))
            column.set_resizable(True)
            column.set_fixed_width(width)
            column.set_expand(expand)
            self.column_view.append_column(column)
            self._column_keys[column] = key
            self._column_thresholds[column] = thresholds[key]
            if key == "cpu":
                self.column_view.sort_by_column(column, Gtk.SortType.DESCENDING)

    # -- responsive columns ----------------------------------------------

    def _fit_columns(self, usable: int) -> bool:
        self._fit_pending = False
        for column, threshold in self._column_thresholds.items():
            column.set_visible(usable >= threshold)
        return False

    def _on_width(self, width: int) -> None:
        if not self._fit_pending:
            self._fit_pending = True
            GLib.idle_add(self._fit_columns, width - TABLE_CHROME,
                          priority=GLib.PRIORITY_HIGH_IDLE)

    def _on_sort_changed(self, sorter: Gtk.Sorter, _change=None) -> None:
        column = sorter.get_primary_sort_column()
        key = self._column_keys.get(column) if column else None
        if key is None:
            return
        descending = sorter.get_primary_sort_order() == Gtk.SortType.DESCENDING
        if (key, descending) == (self._sort_key, self._sort_descending):
            return
        self._sort_key = key
        self._sort_descending = descending
        self._reapply()

    # -- selection -------------------------------------------------------

    def _on_selection_changed(self, *_args) -> None:
        if self._restoring_selection:
            return
        item = self.selection.get_selected_item()
        self._selected_pid = item.row.pid if item is not None else None

    def _restore_selection(self) -> None:
        """Keep the highlight on the same process even as rows move."""
        if self._selected_pid is None:
            return
        target = next((i for i, row in enumerate(self._rows)
                       if row.pid == self._selected_pid), None)
        self._restoring_selection = True
        try:
            if target is None:
                self.selection.unselect_all()
            elif self.selection.get_selected() != target:
                self.selection.set_selected(target)
        finally:
            self._restoring_selection = False

    # -- process actions -------------------------------------------------

    def _end_selected(self, force: bool) -> None:
        item = self.selection.get_selected_item()
        if item is None:
            self._toast("Select a process first")
            return
        pid, name = item.row.pid, item.row.name
        if pid == self._own_pid:
            self._toast("Refusing to terminate Vitals itself")
            return
        dialog = Adw.AlertDialog(
            heading=f"{'Kill' if force else 'End'} {name}?",
            body=f"PID {pid} will receive {'SIGKILL' if force else 'SIGTERM'}. "
                 "Unsaved work in that process may be lost.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("confirm", "Kill" if force else "End Process")
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response: str) -> None:
            if response != "confirm":
                return
            ok, message = sampler_mod.kill_process(pid, force=force)
            self._toast(message if ok else f"⚠ {message}")

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=3))

    # -- refresh ---------------------------------------------------------

    def _matches(self, row: ProcRow, query: str) -> bool:
        return (query in row.name.lower()
                or query in row.user.lower()
                or query in str(row.pid))

    def _reapply(self) -> None:
        """Re-render with the last snapshot after a filter or sort change."""
        if self._latest is not None:
            self.update(self._latest)

    def update(self, s: Snapshot) -> None:
        self._latest = s
        query = self.search.get_text().strip().lower()
        rows = [row for row in s.processes if self._matches(row, query)] \
            if query else list(s.processes)
        rows.sort(key=lambda row: getattr(row, self._sort_key),
                  reverse=self._sort_descending)
        self._rows = rows

        current = self.store.get_n_items()
        if len(rows) > current:
            self.store.splice(current, 0,
                              [ProcObject() for _ in range(len(rows) - current)])
        elif len(rows) < current:
            self.store.splice(len(rows), current - len(rows), [])

        for i, row in enumerate(rows):
            self.store.get_item(i).row = row

        # Row positions are stable, so GTK does not re-bind them; repaint the
        # cells that are currently on screen.
        for list_item, render in list(self._renderers.items()):
            render(list_item)
        self._restore_selection()

        busy = sum(1 for row in s.processes if row.cpu >= 1.0)
        hidden = max(0, s.proc_count - len(s.processes))
        shown = f"{len(rows)} shown · " if query else ""
        suffix = f" · {hidden} kernel hidden" if hidden else ""
        self.summary.set_label(
            f"{shown}{s.proc_count} processes · {s.thread_count} threads · "
            f"{busy} active{suffix}"
        )
