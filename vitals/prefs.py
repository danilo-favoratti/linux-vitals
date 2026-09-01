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
    directory = os.path.dirname(PATH)
    tmp = PATH + ".tmp"
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, PATH)
        os.chmod(PATH, 0o600)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
