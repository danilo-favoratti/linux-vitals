"""Application shell: the portrait window, its actions and the tick loop.

The window is a 9:16 split: the internal web console on top, the system monitor
below, with a draggable divider between them.
"""

from __future__ import annotations

import argparse
import platform
import socket
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import bootstrap, prefs, theme  # noqa: E402
from .apps import AppHub  # noqa: E402
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

HOUSEKEEPING_EVERY = 60
RESOURCE_LOG_EVERY = 600.0


def redraw_tree(widget: Gtk.Widget) -> None:
    """Invalidate every widget below (and including) this one.

    The charts read the palette while they snapshot, so a theme switch stays
    invisible on them until each one is asked to draw again; reloading the CSS
    only refreshes what the stylesheet itself covers.
    """
    widget.queue_draw()
    child = widget.get_first_child()
    while child is not None:
        redraw_tree(child)
        child = child.get_next_sibling()


def _iter_monitors(display: Gdk.Display | None):
    if display is None:
        return
    monitors = display.get_monitors()
    for i in range(monitors.get_n_items()):
        yield monitors.get_item(i)


def pick_monitor(display: Gdk.Display | None):
    """Prefer a portrait screen (the desk panel); otherwise the primary."""
    portrait = None
    first = None
    for monitor in _iter_monitors(display):
        if first is None:
            first = monitor
        geom = monitor.get_geometry()
        if geom.height > geom.width and portrait is None:
            portrait = monitor
    return portrait or first


def portrait_size(display: Gdk.Display | None) -> tuple[int, int]:
    """A 9:16 window that actually fits the screen it will land on."""
    width_limit, height_limit = 1200, 1000
    monitor = pick_monitor(display)
    if monitor is not None:
        geometry = monitor.get_geometry()
        width_limit, height_limit = geometry.width, geometry.height

    height = min(int(height_limit * 0.92), 1400)
    width = round(height * ASPECT)
    if width > width_limit * 0.98:
        width = int(width_limit * 0.98)
        height = round(width / ASPECT)
    if height > height_limit * 0.98:
        height = int(height_limit * 0.98)
        width = round(height * ASPECT)
    return max(360, width), max(520, height)


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
        self.hub = app.hub if isinstance(app, VitalsApp) else AppHub()
        self.monitor = MonitorView(sampler, self.toast_overlay, self.hub)
        self.monitor.pause_button.connect("toggled", self._on_pause_toggled)
        self.monitor.stack.connect("notify::visible-child", self._on_page_changed)

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
        header.pack_end(self._build_theme_button())
        self.toast_overlay.set_child(toolbar)
        self.set_content(self.toast_overlay)

        self._install_shortcuts()
        # Sample only after the surface exists. Doing it from __init__ via idle
        # can run before the first map, and a stall there leaves a process
        # with no visible window.
        self.connect("map", self._on_mapped)
        self.connect("close-request", self._on_close_request)
        self.connect("unrealize", self._clear_tick)

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

        # Wraps rather than clips: one button per tab overruns the portrait width
        # as soon as the window is narrowed.
        buttons = Adw.WrapBox(child_spacing=8, line_spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        for _key, label, _icon, url in TABS:
            button = Gtk.Button(label=f"Open {label}")
            button.connect("clicked", lambda _b, u=url: Gtk.UriLauncher(uri=u).launch(
                self, None, None))
            buttons.append(button)
        box.append(buttons)
        return box

    # -- chrome ----------------------------------------------------------

    def _build_theme_button(self) -> Gtk.Button:
        self.theme_button = Gtk.Button()
        self.theme_button.connect("clicked", lambda *_: self.toggle_theme())
        self._sync_theme_button()
        return self.theme_button

    def _sync_theme_button(self) -> None:
        """Show the theme the button switches to, not the one in use."""
        going_light = theme.is_dark()
        self.theme_button.set_icon_name(
            "weather-clear-symbolic" if going_light
            else "weather-clear-night-symbolic"
        )
        self.theme_button.set_tooltip_text(
            f"Switch to the {'light' if going_light else 'dark'} theme (Ctrl+T)"
        )

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
        other.append("Light theme", "win.light-theme")
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

        self.light_action = Gio.SimpleAction.new_stateful(
            "light-theme", None, GLib.Variant.new_boolean(not theme.is_dark())
        )
        self.light_action.connect("activate", lambda *_: self.toggle_theme())
        self.add_action(self.light_action)

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

        add("<Control>q", self._quit_app)
        add("<Control>w", self.close)
        add("<Control>space", lambda: self.monitor.pause_button.set_active(
            not self.paused))
        add("<Control>f", self.monitor.focus_search)
        add("<Control>t", self.toggle_theme)
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

    # -- theme -----------------------------------------------------------

    def toggle_theme(self) -> None:
        mode = "dark" if theme.mode == "light" else "light"
        app = self.get_application()
        if isinstance(app, VitalsApp):
            app.set_theme(mode)
        self._toast(f"{mode.capitalize()} theme")

    def on_theme_changed(self) -> None:
        """Follow-up work the stylesheet cannot do on its own."""
        self._sync_theme_button()
        self.light_action.set_state(GLib.Variant.new_boolean(not theme.is_dark()))
        if self.web is not None:
            self.web.apply_theme()
        redraw_tree(self)

    # -- actions ---------------------------------------------------------

    def _quit_app(self) -> None:
        app = self.get_application()
        if isinstance(app, VitalsApp):
            app.request_quit()
        else:
            self.close()

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
        if self._tick_source is not None:
            return
        self.sampler.set_want_processes(self.monitor.wants_processes)
        self.sampler.set_want_gpu_clients(self.monitor.wants_gpu_clients)
        self.sampler.start_loop(self.interval_ms)
        self._schedule()
        GLib.idle_add(self._refresh_now)

    def _on_close_request(self, *_args) -> bool:
        """Keep the process. GNOME destroying the surface used to quit us overnight."""
        app = self.get_application()
        if isinstance(app, VitalsApp) and not app._user_quit:
            bootstrap.log("close-request: hiding")
            self.set_visible(False)
            return True
        return False

    def _clear_tick(self, *_args) -> None:
        if self._tick_source is not None:
            GLib.source_remove(self._tick_source)
            self._tick_source = None

    def _on_mapped(self, *_args) -> None:
        self.start()

    def _on_page_changed(self, *_args) -> None:
        self.sampler.set_want_processes(self.monitor.wants_processes)
        self.sampler.set_want_gpu_clients(self.monitor.wants_gpu_clients)
        self._refresh_now()

    def _schedule(self) -> None:
        if self._tick_source is not None:
            GLib.source_remove(self._tick_source)
        self._tick_source = GLib.timeout_add(self.interval_ms, self._on_tick)

    def _on_rate_action(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        self.interval_ms = value.get_int32()
        action.set_state(value)
        self.sampler.set_interval_ms(self.interval_ms)
        self._schedule()
        self._toast(f"Refreshing every {self.interval_ms / 1000:g}s")

    def _on_pause_toggled(self, button: Gtk.ToggleButton) -> None:
        self.paused = button.get_active()
        button.set_icon_name(
            "media-playback-start-symbolic" if self.paused
            else "media-playback-pause-symbolic"
        )
        self.sampler.set_paused(self.paused)
        if self.paused:
            if self._tick_source is not None:
                GLib.source_remove(self._tick_source)
                self._tick_source = None
            self._refresh_now()
        else:
            self._schedule()

    def _on_tick(self) -> bool:
        self._tick_count += 1
        self._refresh()
        return True

    def _refresh_now(self) -> bool:
        self._refresh()
        return False

    def _refresh(self, *_args) -> None:
        snapshot = self.sampler.latest()
        try:
            self.monitor.update(snapshot, processes_fresh=True)
        except Exception as exc:
            bootstrap.log(f"monitor update failed: {type(exc).__name__}: {exc}")
            return
        self.monitor.set_status(
            f"{socket.gethostname()} · {platform.system()} "
            f"{platform.release().split('-')[0]} · up {theme.fmt_secs(snapshot.uptime)}"
            f" · {snapshot.proc_count} proc · {snapshot.sample_ms:.0f} ms"
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
        self.hub = AppHub()
        self.css = Gtk.CssProvider()
        self._user_quit = False
        self._recreate_tries = 0
        self._housekeeping_source = 0
        self._last_resource_log = 0.0
        self.connect("shutdown", self._on_shutdown)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        # Keep the process up when GNOME destroys the window (output sleep,
        # unplug of the portrait panel). Ctrl+Q still calls request_quit().
        self.hold()
        theme.set_mode(prefs.load().get("theme", theme.DEFAULT_MODE))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            monitors = display.get_monitors()
            monitors.connect("items-changed", self._on_monitors_changed)
        self.refresh_theme()
        self._housekeeping_source = GLib.timeout_add_seconds(
            HOUSEKEEPING_EVERY, self._housekeeping
        )
        bootstrap.log("app startup")

    def _housekeeping(self) -> bool:
        """Watch this process's own footprint and reclaim before it is fatal.

        This is what used to end the session after a few days: descriptors and
        memory climbed while the screen was awake, and the process was either
        aborted by a WebKit spawn hitting EMFILE or killed outright once it grew
        into the biggest thing in the session. Both live in the web views, so
        crossing a budget recycles them instead of waiting for the kill.
        """
        count, limit = bootstrap.fd_count(), bootstrap.fd_limit()
        mb = 1024 * 1024
        rss_mb = bootstrap.rss_bytes() // mb
        # Everything charged to Vitals, web processes included; the sampler
        # already totals this for the footer.
        tree = self.sampler.latest().self_rss if self.sampler else bootstrap.rss_bytes()
        tree_mb = int(tree) // mb
        now = GLib.get_monotonic_time() / 1e6
        if now - self._last_resource_log >= RESOURCE_LOG_EVERY:
            self._last_resource_log = now
            bootstrap.log(
                f"resources fds={count}/{limit} rss={rss_mb}M tree={tree_mb}M "
                f"[{bootstrap.fd_breakdown()}]"
            )
        reason = ""
        if bootstrap.fd_pressure():
            reason = f"fd pressure {count}/{limit}"
        elif bootstrap.memory_pressure(tree):
            reason = f"memory pressure {tree_mb}M"
        if reason:
            for window in self.get_windows():
                web = getattr(window, "web", None)
                if web is not None:
                    web.recycle(reason)
        return True

    def set_theme(self, mode: str) -> None:
        theme.set_mode(mode)
        prefs.save(theme=theme.mode)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        """Push the active palette into libadwaita, the CSS and the windows."""
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if theme.is_dark()
            else Adw.ColorScheme.FORCE_LIGHT
        )
        self.css.load_from_data(theme.css().encode())
        for window in self.get_windows():
            if isinstance(window, VitalsWindow):
                window.on_theme_changed()

    def do_activate(self) -> None:
        try:
            self._ensure_window()
        except Exception:
            import traceback
            bootstrap.log("activate failed:\n" + traceback.format_exc())
            traceback.print_exc()
            raise

    def request_quit(self) -> None:
        if self._user_quit:
            return
        bootstrap.log("user quit")
        self._user_quit = True
        self.release()
        self.quit()

    def _on_monitors_changed(self, *_args) -> None:
        bootstrap.log("monitors changed")
        if not self._user_quit:
            GLib.timeout_add(500, self._ensure_window)

    def _on_window_destroy(self, window: Gtk.Window) -> None:
        bootstrap.log(f"window destroyed fds={bootstrap.fd_count()}/{bootstrap.fd_limit()}")
        if self._user_quit:
            return
        self._recreate_tries += 1
        delay = min(8.0, 0.4 * (2 ** min(self._recreate_tries, 4)))
        GLib.timeout_add(int(delay * 1000), self._ensure_window)

    def _ensure_window(self) -> bool:
        if self._user_quit:
            return False
        window = self.props.active_window
        if window is None:
            windows = self.get_windows()
            window = windows[0] if windows else None
        if window is None:
            bootstrap.log(f"creating window fds={bootstrap.fd_count()}/{bootstrap.fd_limit()}")
            if self.sampler is None:
                self.sampler = Sampler()
            window = VitalsWindow(self, self.sampler,
                                  monitor_only=self.monitor_only)
            window.connect("destroy", self._on_window_destroy)
        window.set_visible(True)
        window.present()
        self._recreate_tries = 0
        return False

    def _on_shutdown(self, *_args) -> None:
        bootstrap.log("app shutdown")
        if self._housekeeping_source:
            GLib.source_remove(self._housekeeping_source)
            self._housekeeping_source = 0
        self.hub.shutdown()
        if self.sampler is not None:
            self.sampler.shutdown()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    parser = argparse.ArgumentParser(prog="vitals", description=__doc__)
    parser.add_argument("--monitor-only", action="store_true",
                        help="hide the web console and show only the system monitor")
    args, rest = parser.parse_known_args(argv[1:])
    return VitalsApp(monitor_only=args.monitor_only).run([argv[0], *rest])
