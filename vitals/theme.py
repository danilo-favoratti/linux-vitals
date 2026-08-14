"""Color palette, CSS and value formatting helpers."""

from __future__ import annotations


def rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


BG_WINDOW = "#0e1014"
BG_CARD = "#171a21"
BG_CARD_ALT = "#1e222b"
GRID = "#272c37"
BORDER = "#242832"
TEXT = "#e7eaee"
TEXT_DIM = "#8d95a3"
TEXT_FAINT = "#5d6472"

CPU = "#4dabf7"
CPU_SYS = "#f76707"
MEM = "#51cf66"
MEM_CACHE = "#2f6b45"
SWAP = "#ffa94d"
NET_RX = "#22b8cf"
NET_TX = "#f783ac"
GPU = "#a78bfa"
VRAM = "#e879f9"
DISK = "#fcc419"
TEMP = "#ff6b6b"

# Distinct-ish hues for the per-core graphs; cycles for >12 cores.
CORE_COLORS = [
    "#4dabf7", "#51cf66", "#ffa94d", "#f783ac",
    "#22b8cf", "#a78bfa", "#fcc419", "#ff8787",
    "#63e6be", "#74c0fc", "#e599f7", "#ffd43b",
]


def core_color(index: int) -> str:
    return CORE_COLORS[index % len(CORE_COLORS)]


def heat_color(pct: float) -> str:
    """Green -> yellow -> orange -> red ramp used for load indicators."""
    if pct < 40:
        return "#51cf66"
    if pct < 65:
        return "#94d82d"
    if pct < 80:
        return "#fcc419"
    if pct < 92:
        return "#ff922b"
    return "#ff6b6b"


CSS = f"""
window.vitals {{ background-color: {BG_WINDOW}; }}
.card {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
.card-title {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.09em;
    color: {TEXT_DIM};
}}
.metric-big {{
    font-size: 27px;
    font-weight: 300;
    font-feature-settings: "tnum";
    color: {TEXT};
}}
.metric-mid {{
    font-size: 15px;
    font-weight: 600;
    font-feature-settings: "tnum";
    color: {TEXT};
}}
.metric-unit {{ font-size: 12px; color: {TEXT_DIM}; }}
.metric-sub {{
    font-size: 11px;
    color: {TEXT_DIM};
    font-feature-settings: "tnum";
}}
.cell-text, .cell-num {{ font-size: 12px; }}
.cell-num {{ font-family: monospace; font-feature-settings: "tnum"; }}
.chip {{
    background-color: {BG_CARD_ALT};
    border-radius: 6px;
    padding: 1px 7px;
    font-size: 10px;
    font-weight: 600;
    color: {TEXT_DIM};
}}
.statusbar {{
    background-color: {BG_CARD};
    border-top: 1px solid {BORDER};
    font-size: 11px;
    color: {TEXT_DIM};
}}
.web-progress, .web-progress trough, .web-progress progress {{ min-height: 3px; }}
.web-progress trough {{ background: transparent; border: none; }}
.web-progress progress {{
    background-color: {CPU};
    border: none;
    border-radius: 0;
}}
.web-error {{ background-color: {BG_WINDOW}; }}
.web-error-code {{
    font-size: 40px;
    font-weight: 200;
    color: {TEXT_DIM};
    font-feature-settings: "tnum";
}}
.web-tabs {{ font-size: 12px; }}
paned > separator {{
    background-color: {BORDER};
    min-height: 4px;
    min-width: 4px;
}}
paned > separator:hover {{ background-color: {CPU}; }}
.pane-bar {{
    background-color: {BG_CARD};
    border-top: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
}}
columnview {{ background-color: transparent; font-feature-settings: "tnum"; }}
columnview > header > button {{
    font-size: 11px;
    font-weight: 700;
    color: {TEXT_DIM};
    background: {BG_CARD_ALT};
    border: none;
    border-radius: 0;
}}
columnview row:selected {{ background-color: rgba(77, 171, 247, 0.22); }}
/* Tighter rows: the table shares the window with the web console. */
columnview > listview > row > cell {{ padding-top: 3px; padding-bottom: 3px; }}
"""

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def fmt_bytes(value: float, precision: int | None = None) -> str:
    v = float(value)
    idx = 0
    while v >= 1024 and idx < len(_UNITS) - 1:
        v /= 1024
        idx += 1
    if precision is None:
        precision = 0 if (idx == 0 or v >= 100) else 1
    return f"{v:.{precision}f} {_UNITS[idx]}"


def fmt_rate(bytes_per_sec: float) -> str:
    return f"{fmt_bytes(bytes_per_sec)}/s"


def fmt_hz(mhz: float) -> str:
    if mhz >= 1000:
        return f"{mhz / 1000:.2f} GHz"
    return f"{mhz:.0f} MHz"


def fmt_secs(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
