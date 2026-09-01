"""Static configuration: app identity, web tabs and refresh rates."""

from __future__ import annotations

APP_ID = "dev.favoratti.Vitals"
APP_NAME = "Vitals"
VERSION = "2.0.0"

# The pages of the internal web console, in tab order. The first one is the tab
# the window opens on.
TABS = (
    ("dashboard", "Dashboard", "view-grid-symbolic",
     "https://cupidoodle.com/internal/dashboard"),
    ("logs", "Logs", "view-list-symbolic",
     "https://cupidoodle.com/internal/logs"),
    ("funnel", "Funnel", "view-sort-descending-symbolic",
     "https://cupidoodle.com/internal/selling-funnel"),
    ("internal", "Internal", "emblem-system-symbolic",
     "https://www.cupidoodle.com/internal"),
)

INTERVALS = (500, 1000, 2000, 3000)
DEFAULT_INTERVAL = 1000

# Sibling projects under the same parent as this repo. Health uses a different
# pair of ports so both stacks can run at once; finances keeps the ports its
# own run.sh already documents.
LOCAL_APPS = (
    {
        "key": "finances",
        "title": "Finances",
        "root": "finances",
        "backend_port": 8000,
        "frontend_port": 5173,
    },
    {
        "key": "health",
        "title": "Health",
        "root": "health",
        "backend_port": 8001,
        "frontend_port": 5174,
    },
)

# 9:16 portrait, sized to the screen it opens on.
ASPECT = 9 / 16
