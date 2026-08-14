#!/usr/bin/env python3
"""Verify the process table renders the right value in the right row.

Each bound cell reports its model position, so the rendered label can be
compared against the value the model holds at that position. Combined with a
check that the model itself is ordered, this proves the table is correct
without needing to inspect pixels.
"""

from __future__ import annotations

import pathlib
import sys

import gi

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from vitals.pages.processes import COLUMNS, ProcessPage  # noqa: E402
from vitals.sampler import Sampler  # noqa: E402

FORMATTERS = {title: formatter for title, _key, formatter, *_rest in COLUMNS}


def check_cells(page: ProcessPage) -> tuple[int, int]:
    """Compare every rendered cell against the model row at its position."""
    checked = 0
    bad = 0
    for list_item in list(page._renderers):
        position = list_item.get_position()
        label = list_item.get_child()
        item = list_item.get_item()
        if label is None or item is None or position >= len(page._rows):
            continue
        expected_row = page._rows[position]
        checked += 1
        if item.row.pid != expected_row.pid:
            bad += 1
            if bad <= 3:
                print(f"  cell at position {position} holds pid "
                      f"{item.row.pid}, model has {expected_row.pid}")
            continue
        text = label.get_text()
        matches = any(text == formatter(expected_row)
                      for formatter in FORMATTERS.values())
        if not matches:
            bad += 1
            if bad <= 3:
                print(f"  position {position}: rendered {text!r} matches no "
                      f"field of pid {expected_row.pid}")
    return checked, bad


def main() -> int:
    app = Adw.Application(application_id="dev.test.ProcRender")
    status = {"failures": 0}

    def on_activate(_app):
        sampler = Sampler()
        page = ProcessPage(sampler, Adw.ToastOverlay())
        window = Gtk.ApplicationWindow(application=app, default_width=1150,
                                       default_height=700)
        window.set_child(page)
        window.present()
        state = {"tick": 0}

        def tick() -> bool:
            state["tick"] += 1
            page.update(sampler.sample())

            rows = page._rows
            ordered = all(
                rows[i].cpu >= rows[i + 1].cpu for i in range(len(rows) - 1)
            )
            checked, bad = check_cells(page)
            top = [(r.pid, round(r.cpu, 1), r.name) for r in rows[:3]]
            print(f"tick {state['tick']}: model sorted desc by cpu: {ordered}")
            print(f"tick {state['tick']}: top={top}")
            print(f"tick {state['tick']}: cells checked={checked} mismatched={bad}\n")
            if not ordered or bad:
                status["failures"] += 1

            if state["tick"] >= 4:
                # Also exercise re-sorting by a different column.
                print("sorting by Memory descending...")
                for column, key in page._column_keys.items():
                    if key == "rss":
                        page.column_view.sort_by_column(
                            column, Gtk.SortType.DESCENDING)
                        break
                page.update(sampler.sample())
                rows = page._rows
                mem_ordered = all(rows[i].rss >= rows[i + 1].rss
                                  for i in range(len(rows) - 1))
                checked, bad = check_cells(page)
                print(f"  sorted by rss desc: {mem_ordered}, "
                      f"cells checked={checked} mismatched={bad}")
                print(f"  top={[(r.name, theme_bytes(r.rss)) for r in rows[:3]]}")
                if not mem_ordered or bad:
                    status["failures"] += 1
                app.quit()
                return False
            return True

        GLib.timeout_add(1100, tick)

    def theme_bytes(value: float) -> str:
        from vitals import theme
        return theme.fmt_bytes(value)

    globals()["theme_bytes"] = theme_bytes
    app.connect("activate", on_activate)
    app.run([])
    print("\nRESULT:", "OK" if status["failures"] == 0
          else f"{status['failures']} failing checks")
    return 0 if status["failures"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
