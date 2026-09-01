"""Color palettes, CSS and value formatting helpers.

Colors are handed out as ``Color`` tokens rather than plain hex strings. Charts
keep the color they were built with for the lifetime of the widget, so a token
that resolves against the active palette on every use is what lets a theme
switch reach a ring or a trace created at startup.
"""

from __future__ import annotations

DARK = {
    "bg_window": "#0e1014",
    "chrome_bg": "#171a21",
    "pane_bg": "#0e1014",
    "bg_card": "#171a21",
    "bg_card_alt": "#1e222b",
    "grid": "#272c37",
    "border": "#242832",
    "text": "#e7eaee",
    "text_dim": "#8d95a3",
    "text_faint": "#5d6472",
    "cpu": "#4dabf7",
    "cpu_sys": "#f76707",
    "mem": "#51cf66",
    "mem_cache": "#2f6b45",
    "swap": "#ffa94d",
    "net_rx": "#22b8cf",
    "net_tx": "#f783ac",
    "gpu": "#a78bfa",
    "vram": "#e879f9",
    "disk": "#fcc419",
    "temp": "#ff6b6b",
    "mint": "#63e6be",
    "sky": "#74c0fc",
    "orchid": "#e599f7",
    "gold": "#ffd43b",
    "lime": "#94d82d",
}

# A warm cream monitor with dusty pastel accents. Only the monitor's own
# surfaces are cream: ``bg_window`` and ``chrome_bg`` stay neutral because they
# back the web console, which keeps the plain light look of the pages it shows.
# Accents are muted rather than reused, since hues tuned for thin lines on
# near-black go washed out on a pale card.
LIGHT = {
    "bg_window": "#f6f6f8",
    "chrome_bg": "#fbfbfd",
    "pane_bg": "#f0e9dc",
    "bg_card": "#fdf9f1",
    "bg_card_alt": "#eae0cf",
    "grid": "#e3d9c7",
    "border": "#e8dfcd",
    "text": "#3a352d",
    "text_dim": "#7b7266",
    "text_faint": "#a79d8d",
    "cpu": "#4a7fa5",
    "cpu_sys": "#c2643a",
    "mem": "#5a9367",
    "mem_cache": "#a9cdb0",
    "swap": "#d08a4f",
    "net_rx": "#3f8f96",
    "net_tx": "#c2647f",
    "gpu": "#8069b0",
    "vram": "#a85fa0",
    "disk": "#ad8a35",
    "temp": "#c25b52",
    "mint": "#4f9e8c",
    "sky": "#6f9fc4",
    "orchid": "#a878b8",
    "gold": "#cfa96a",
    "lime": "#8aa54a",
}

MODES = ("dark", "light")
DEFAULT_MODE = "dark"

mode = DEFAULT_MODE
_palette = DARK


class Color:
    """A palette entry, resolved against the active palette on every use."""

    __slots__ = ("token",)

    def __init__(self, token: str) -> None:
        self.token = token

    @property
    def hex(self) -> str:
        return _palette[self.token]

    def __str__(self) -> str:
        return self.hex

    def __repr__(self) -> str:
        return f"Color({self.token!r} -> {self.hex})"


# What the drawing helpers accept: a palette token, or a literal hex string.
Paint = Color | str


def set_mode(name: str) -> str:
    """Switch the active palette; unknown names fall back to the default."""
    global mode, _palette
    mode = name if name in MODES else DEFAULT_MODE
    _palette = LIGHT if mode == "light" else DARK
    return mode


def is_dark() -> bool:
    return mode == "dark"


def rgb(color: Color | str) -> tuple[float, float, float]:
    h = str(color).lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


def css_rgba(color: Color | str, alpha: float) -> str:
    r, g, b = rgb(color)
    return f"rgba({r * 255:.0f}, {g * 255:.0f}, {b * 255:.0f}, {alpha})"


BG_WINDOW = Color("bg_window")
CHROME_BG = Color("chrome_bg")
PANE_BG = Color("pane_bg")
BG_CARD = Color("bg_card")
BG_CARD_ALT = Color("bg_card_alt")
GRID = Color("grid")
BORDER = Color("border")
TEXT = Color("text")
TEXT_DIM = Color("text_dim")
TEXT_FAINT = Color("text_faint")

CPU = Color("cpu")
CPU_SYS = Color("cpu_sys")
MEM = Color("mem")
MEM_CACHE = Color("mem_cache")
SWAP = Color("swap")
NET_RX = Color("net_rx")
NET_TX = Color("net_tx")
GPU = Color("gpu")
VRAM = Color("vram")
DISK = Color("disk")
TEMP = Color("temp")
MINT = Color("mint")
SKY = Color("sky")
ORCHID = Color("orchid")
GOLD = Color("gold")
LIME = Color("lime")

# Distinct-ish hues for the per-core graphs; cycles for >12 cores.
CORE_COLORS = [CPU, MEM, SWAP, NET_TX, NET_RX, GPU,
               DISK, TEMP, MINT, SKY, ORCHID, GOLD]

# Green -> yellow -> orange -> red ramp used for load indicators.
_HEAT = ((40, MEM), (65, LIME), (80, DISK), (92, SWAP))


def core_color(index: int) -> Color:
    return CORE_COLORS[index % len(CORE_COLORS)]


def heat_color(pct: float) -> Color:
    for limit, color in _HEAT:
        if pct < limit:
            return color
    return TEMP


def css() -> str:
    """The application stylesheet for the active palette."""
    return f"""
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
}}
.statusbar label {{
    font-size: 11px;
    color: {TEXT_DIM};
}}
.statusbar-clock, .statusbar-self {{
    font-feature-settings: "tnum";
    font-variant-numeric: tabular-nums;
}}
.statusbar-self {{
    font-weight: 600;
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
/* Four labelled tabs only fit the portrait header with tighter toggles than the
   8px libadwaita default. */
.web-tabs toggle {{ padding-left: 6px; padding-right: 6px; }}
paned > separator {{
    background-color: {BORDER};
    min-height: 4px;
    min-width: 4px;
}}
paned > separator:hover {{ background-color: {CPU}; }}
.pane-bar, .switcher-bar {{
    border-top: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
}}
/* The window header bar sits above the web console, so it keeps the neutral
   surface; the switcher strip belongs to the monitor and takes its card color. */
.pane-bar {{ background-color: {CHROME_BG}; }}
.switcher-bar {{ background-color: {BG_CARD}; }}
/* The monitor half only. Setting libadwaita's colors here rather than on the
   window keeps its stock widgets (search entry, buttons) on the same palette as
   the cards around them, without reaching the web console above. */
.monitor-pane {{
    background-color: {PANE_BG};
    --window-bg-color: {PANE_BG};
    --window-fg-color: {TEXT};
    --view-bg-color: {BG_CARD};
    --view-fg-color: {TEXT};
    --card-bg-color: {BG_CARD};
    --dialog-bg-color: {BG_CARD};
    --popover-bg-color: {BG_CARD};
    --accent-color: {CPU};
    --accent-bg-color: {CPU};
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
columnview row:selected {{ background-color: {css_rgba(CPU, 0.22)}; }}
/* Tighter rows: the table shares the window with the web console. */
columnview > listview > row > cell {{ padding-top: 3px; padding-bottom: 3px; }}
.app-service {{
    background-color: {css_rgba(TEXT, 0.045)};
    border: 1px solid {css_rgba(TEXT, 0.08)};
    border-left: 3px solid {TEXT_FAINT};
    border-radius: 8px;
    padding: 8px 10px;
}}
.app-service-running {{
    background-color: {css_rgba(MEM, 0.10)};
    border-color: {css_rgba(MEM, 0.22)};
    border-left-color: {MEM};
}}
.app-service-busy {{
    background-color: {css_rgba(SWAP, 0.10)};
    border-color: {css_rgba(SWAP, 0.22)};
    border-left-color: {SWAP};
}}
.app-service-error {{
    background-color: {css_rgba(TEMP, 0.10)};
    border-color: {css_rgba(TEMP, 0.22)};
    border-left-color: {TEMP};
}}
.app-dot {{
    min-width: 9px;
    min-height: 9px;
    border-radius: 99px;
    background-color: {TEXT_FAINT};
}}
.app-dot-running {{ background-color: {MEM}; }}
.app-dot-starting, .app-dot-stopping {{ background-color: {SWAP}; }}
.app-dot-error {{ background-color: {TEMP}; }}
.app-role {{
    font-size: 12px;
    font-weight: 700;
    color: {TEXT};
}}
.app-port {{
    font-size: 11px;
    font-family: monospace;
    font-feature-settings: "tnum";
    color: {TEXT_DIM};
}}
.app-chip {{
    border-radius: 999px;
    padding: 2px 8px;
    color: {TEXT_DIM};
    background-color: {css_rgba(TEXT, 0.08)};
}}
.app-chip label {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.04em;
}}
.app-chip-running {{
    color: {MEM};
    background-color: {css_rgba(MEM, 0.22)};
}}
.app-chip-busy {{
    color: {SWAP};
    background-color: {css_rgba(SWAP, 0.22)};
}}
.app-chip-error {{
    color: {TEMP};
    background-color: {css_rgba(TEMP, 0.22)};
}}
.app-open {{
    font-size: 12px;
    font-weight: 600;
    color: {TEXT_DIM};
    background-color: {css_rgba(TEXT, 0.06)};
    border: none;
    box-shadow: none;
}}
.app-open:hover {{
    background-color: {css_rgba(TEXT, 0.10)};
}}
.app-open:disabled {{
    opacity: 0.45;
}}
.app-open-ready {{
    color: {MEM};
    background-color: {css_rgba(MEM, 0.18)};
}}
.app-open-ready:hover {{
    background-color: {css_rgba(MEM, 0.28)};
}}
.app-toggle {{
    border: none;
    box-shadow: none;
    min-width: 28px;
    min-height: 28px;
}}
.app-toggle-start {{
    color: {MEM};
    background-color: {css_rgba(MEM, 0.18)};
}}
.app-toggle-start:hover {{
    background-color: {css_rgba(MEM, 0.30)};
}}
.app-toggle-stop {{
    color: {TEMP};
    background-color: {css_rgba(TEMP, 0.16)};
}}
.app-toggle-stop:hover {{
    background-color: {css_rgba(TEMP, 0.28)};
}}
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


def fmt_live(seconds: float) -> str:
    """Uptime of this process; seconds stay visible for the first hour."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
