"""Application shell: the portrait window, its actions and the tick loop.

The window is a 9:16 split: the internal web console on top, the system monitor
below, with a draggable divider between them.
"""

from __future__ import annotations

import argparse
import platform
import socket
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import bootstrap, theme  # noqa: E402
from .config import (  # noqa: E402
    APP_ID,
    APP_NAME,
    ASPECT,
    DEFAULT_INTERVAL,
    INTERVALS,
    TABS,
    VERSION,
)
from .monitor import MonitorView, PAGES  # noqa: E402
from .sampler import Sampler  # noqa: E402

# Process enumeration dominates the tick cost, so when its table is hidden we
# only refresh it occasionally to keep counters and GPU client names current.
BACKGROUND_PROC_EVERY = 4


def portrait_size(display: Gdk.Display | None) -> tuple[int, int]:
    """A 9:16 window as tall as the screen comfortably allows."""
    width_limit, height_limit = 1200, 1000
    if display is not None:
        monitors = display.get_monitors()
        if monitors.get_n_items():
            geometry = monitors.get_item(0).get_geometry()
            width_limit, height_limit = geometry.width, geometry.height

    height = min(int(height_limit * 0.92), 1400)
    width = round(height * ASPECT)
    if width > width_limit * 0.55:
        width = int(width_limit * 0.55)
        height = round(width / ASPECT)
    return max(420, width), max(680, height)


class VitalsWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, sampler: Sampler,
                 monitor_only: bool = False) -> None:
        super().__init__(application=app, title=APP_NAME)
        self.sampler = sampler
        self.interval_ms = DEFAULT_INTERVAL
        self.paused = False
        self.web: object | None = None
        self._tick_source: int | None = None
        self._tick_count = 0

        width, height = portrait_size(self.get_display())
        self.set_default_size(width, height)
        self.set_size_request(400, 520)
        self.set_icon_name(APP_ID)
        self.add_css_class("vitals")

        self.toast_overlay = Adw.ToastOverlay()
        self.monitor = MonitorView(sampler, self.toast_overlay)
        self.monitor.pause_button.connect("toggled", self._on_pause_toggled)
        self.monitor.stack.connect("notify::visible-child",
                                   lambda *_: self._refresh_now())

        header = Adw.HeaderBar()
        header.add_css_class("pane-bar")
        self.paned: Gtk.Paned | None = None

        if monitor_only:
            content = self.monitor
            header.set_title_widget(Gtk.Label(label=APP_NAME))
        else:
            top = self._build_web_pane(header)
            self.paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL,
                                   wide_handle=True)
            self.paned.set_start_child(top)
            self.paned.set_end_child(self.monitor)
            self.paned.set_shrink_start_child(False)
            self.paned.set_shrink_end_child(False)
            self.paned.set_position(int((height - 46) / 2))
            content = self.paned

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(content)
        header.pack_end(self._build_menu())
        self.toast_overlay.set_child(toolbar)
        self.set_content(self.toast_overlay)

        self._install_shortcuts()
        self.start()

    # -- web half --------------------------------------------------------

    def _build_web_pane(self, header: Adw.HeaderBar) -> Gtk.Widget:
        if not bootstrap.webkit_available():
            header.set_title_widget(Gtk.Label(label=APP_NAME))
            return self._build_web_placeholder()

        from .webpane import WebPane

        pane = WebPane()
        self.web = pane
        header.set_title_widget(pane.build_switcher())

        reload_button = Gtk.Button(icon_name="view-refresh-symbolic")
        reload_button.set_tooltip_text("Reload this tab (Ctrl+R)")
        reload_button.connect("clicked", lambda *_: pane.reload())
        header.pack_start(reload_button)
        return pane

    def _build_web_placeholder(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_start(24)
        box.set_margin_end(24)
        title = Gtk.Label(label="Web console unavailable")
        title.add_css_class("metric-mid")
        hint = Gtk.Label(label=bootstrap.MISSING_WEBKIT_HINT, justify=Gtk.Justification.CENTER)
        hint.add_css_class("metric-sub")
        hint.set_wrap(True)
        box.append(title)
        box.append(hint)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        for _key, label, _icon, url in TABS:
            button = Gtk.Button(label=f"Open {label}")
            button.connect("clicked", lambda _b, u=url: Gtk.UriLauncher(uri=u).launch(
                self, None, None))
            buttons.append(button)
        box.append(buttons)
        return box

    # -- chrome ----------------------------------------------------------

    def _build_menu(self) -> Gtk.MenuButton:
        menu = Gio.Menu()

        if self.web is not None:
            web = Gio.Menu()
            web.append("Reload tab", "win.web-reload")
            web.append("Back", "win.web-back")
            web.append("Reset tab to its URL", "win.web-home")
            web.append("Open in browser", "win.web-external")
            zoom = Gio.Menu()
            zoom.append("Zoom in", "win.web-zoom(1)")
            zoom.append("Zoom out", "win.web-zoom(-1)")
            zoom.append("Reset zoom", "win.web-zoom(0)")
            web.append_section(None, zoom)
            menu.append_section("Web console", web)

        rates = Gio.Menu()
        for ms in INTERVALS:
            label = f"{ms / 1000:g} second{'s' if ms != 1000 else ''}"
            rates.append_item(Gio.MenuItem.new(label, f"win.rate({ms})"))
        menu.append_section("Refresh interval", rates)

        other = Gio.Menu()
        other.append("Pause updates", "win.pause")
        if self.paned is not None:
            other.append("Even split", "win.even-split")
            other.append("Reset to 9:16", "win.reset-shape")
        other.append("About Vitals", "win.about")
        menu.append_section(None, other)

        self._add_actions()
        button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        button.set_menu_model(menu)
        button.set_tooltip_text("Options")
        return button

    def _add_actions(self) -> None:
        def simple(name: str, callback) -> None:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_: callback())
            self.add_action(action)

        rate_action = Gio.SimpleAction.new_stateful(
            "rate", GLib.VariantType.new("i"), GLib.Variant.new_int32(DEFAULT_INTERVAL)
        )
        rate_action.connect("activate", self._on_rate_action)
        self.add_action(rate_action)

        simple("pause", lambda: self.monitor.pause_button.set_active(not self.paused))
        simple("about", self._show_about)

        if self.paned is not None:
            simple("even-split", self._even_split)
            simple("reset-shape", self._reset_shape)

        if self.web is not None:
            simple("web-reload", self.web.reload)
            simple("web-back", self.web.go_back)
            simple("web-home", self.web.go_home)
            simple("web-external", self.web.open_externally)
            zoom_action = Gio.SimpleAction.new("web-zoom", GLib.VariantType.new("i"))
            zoom_action.connect("activate", self._on_zoom_action)
            self.add_action(zoom_action)

    def _install_shortcuts(self) -> None:
        controller = Gtk.ShortcutController()
        # Capture phase: page and tab switching must work even while the web
        # view has the keyboard focus.
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def add(accel: str, callback) -> None:
            controller.add_shortcut(Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string(accel),
                action=Gtk.CallbackAction.new(lambda *_: (callback(), True)[1]),
            ))

        add("<Control>q", self.close)
        add("<Control>w", self.close)
        add("<Control>space", lambda: self.monitor.pause_button.set_active(
            not self.paused))
        add("<Control>f", self.monitor.focus_search)
        add("F5", self._refresh_now)
        for index, name in enumerate(PAGES, start=1):
            add(f"<Control>{index}", lambda n=name: self.monitor.show_page(n))

        if self.web is not None:
            add("<Control>r", self.web.reload)
            add("<Alt>Left", self.web.go_back)
            for index, (key, *_rest) in enumerate(TABS, start=1):
                add(f"<Alt>{index}", lambda k=key: self.web.show_tab(k))
            add("<Control>plus", lambda: self.web.apply_zoom(0.1))
            add("<Control>equal", lambda: self.web.apply_zoom(0.1))
            add("<Control>minus", lambda: self.web.apply_zoom(-0.1))
            add("<Control>0", lambda: self.web.apply_zoom(reset=True))

        self.add_controller(controller)

    # -- actions ---------------------------------------------------------

    def _on_zoom_action(self, _action: Gio.SimpleAction, value: GLib.Variant) -> None:
        step = value.get_int32()
        level = self.web.apply_zoom(step * 0.1, reset=step == 0)
        self._toast(f"Web zoom {level * 100:.0f}%")

    def _even_split(self) -> None:
        if self.paned is not None:
            self.paned.set_position(int(self.paned.get_height() / 2))

    def _reset_shape(self) -> None:
        self.unmaximize()
        width, height = portrait_size(self.get_display())
        self.set_default_size(width, height)
        GLib.idle_add(self._even_split)

    def _show_about(self) -> None:
        gpu = self.sampler.snapshot.gpu
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            developer_name="Built with GTK4, libadwaita, WebKitGTK and psutil",
            comments=(
                "Internal web console over a real-time monitor for CPU, memory, "
                "swap, network, disk, processes and GPU.\n\n"
                f"Detected GPU: {gpu.name if gpu.available else 'none'}"
            ),
            license_type=Gtk.License.MIT_X11,
        )
        about.present(self)

    # -- tick loop -------------------------------------------------------

    def start(self) -> None:
        self._schedule()
        GLib.idle_add(self._refresh_now)

    def _schedule(self) -> None:
        if self._tick_source is not None:
            GLib.source_remove(self._tick_source)
        self._tick_source = GLib.timeout_add(self.interval_ms, self._on_tick)

    def _on_rate_action(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        self.interval_ms = value.get_int32()
        action.set_state(value)
        self._schedule()
        self._toast(f"Refreshing every {self.interval_ms / 1000:g}s")

    def _on_pause_toggled(self, button: Gtk.ToggleButton) -> None:
        self.paused = button.get_active()
        button.set_icon_name(
            "media-playback-start-symbolic" if self.paused
            else "media-playback-pause-symbolic"
        )
        if self.paused:
            if self._tick_source is not None:
                GLib.source_remove(self._tick_source)
                self._tick_source = None
        else:
            self._schedule()

    def _on_tick(self) -> bool:
        self._tick_count += 1
        self._refresh(from_timer=True)
        return True

    def _refresh_now(self) -> bool:
        self._refresh(from_timer=False)
        return False

    def _refresh(self, from_timer: bool) -> None:
        want_procs = (
            self.monitor.wants_processes
            or not from_timer
            or self._tick_count % BACKGROUND_PROC_EVERY == 0
        )
        started = time.perf_counter()
        snapshot = self.sampler.sample(want_processes=want_procs)
        self.monitor.update(snapshot, processes_fresh=want_procs)
        cost_ms = (time.perf_counter() - started) * 1000

        self.monitor.set_status(
            f"{socket.gethostname()} · {platform.system()} "
            f"{platform.release().split('-')[0]} · up {theme.fmt_secs(snapshot.uptime)}"
            f" · {snapshot.proc_count} proc · {cost_ms:.0f} ms"
            f" · {self.interval_ms / 1000:g}s"
            + ("  · PAUSED" if self.paused else "")
        )

    def _toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=2))


class VitalsApp(Adw.Application):
    def __init__(self, monitor_only: bool = False) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.monitor_only = monitor_only
        self.sampler: Sampler | None = None
        self.connect("shutdown", self._on_shutdown)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        provider = Gtk.CssProvider()
        provider.load_from_data(theme.CSS.encode())
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            self.sampler = Sampler()
            window = VitalsWindow(self, self.sampler, monitor_only=self.monitor_only)
        window.present()

    def _on_shutdown(self, *_args) -> None:
        if self.sampler is not None:
            self.sampler.shutdown()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    parser = argparse.ArgumentParser(prog="vitals", description=__doc__)
    parser.add_argument("--monitor-only", action="store_true",
                        help="hide the web console and show only the system monitor")
    args, rest = parser.parse_known_args(argv[1:])
    return VitalsApp(monitor_only=args.monitor_only).run([argv[0], *rest])
