"""Static configuration: app identity, web tabs and refresh rates."""

from __future__ import annotations

APP_ID = "dev.favoratti.Vitals"
APP_NAME = "Vitals"
VERSION = "2.0.0"

# The two pages of the internal web console, in tab order.
TABS = (
    ("logs", "Logs", "view-list-symbolic",
     "https://cupidoodle.com/internal/logs"),
    ("dashboard", "Dashboard", "view-grid-symbolic",
     "https://cupidoodle.com/internal/dashboard"),
)

INTERVALS = (500, 1000, 2000, 3000)
DEFAULT_INTERVAL = 1000

# 9:16 portrait, sized to the screen it opens on.
ASPECT = 9 / 16
