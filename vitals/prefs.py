"""Persisted user choices, kept in a small JSON file under the config dir.

Not worth a GSettings schema: this is one setting, and a schema would have to be
compiled into the system before the app could start.
"""

from __future__ import annotations

import json
import os

from gi.repository import GLib

PATH = os.path.join(GLib.get_user_config_dir(), "vitals", "settings.json")


def load() -> dict:
    """The stored settings, or an empty dict if there is nothing usable."""
    try:
        with open(PATH, encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def save(**values) -> None:
    """Merge values into the file. A read-only config dir is not fatal."""
    merged = load() | values
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with open(PATH, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2)
    except OSError:
        pass
