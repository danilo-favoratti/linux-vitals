"""Top half of the window: the internal web console.

Both tabs share a single ``WebKit.NetworkSession`` so that signing in once
applies to both pages and survives restarts. Pages are loaded lazily, on first
activation, to keep startup cheap: each web view spawns its own web process as
soon as it has something to render.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gio, GLib, Gtk, WebKit  # noqa: E402

from . import draw, theme  # noqa: E402
from .config import TABS  # noqa: E402

MIN_ZOOM, MAX_ZOOM, DEFAULT_ZOOM = 0.5, 2.0, 0.9

# Statuses where the useful next step is "log in", not "retry".
AUTH_STATUSES = (401, 403, 407)


def make_session() -> WebKit.NetworkSession:
    """A session with cookies on disk, so a login outlives the process."""
    data_dir = os.path.join(GLib.get_user_data_dir(), "vitals", "webkit")
    cache_dir = os.path.join(GLib.get_user_cache_dir(), "vitals", "webkit")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    session = WebKit.NetworkSession.new(data_dir, cache_dir)
    cookies = session.get_cookie_manager()
    cookies.set_persistent_storage(
        os.path.join(data_dir, "cookies.sqlite"), WebKit.CookiePersistentStorage.SQLITE
    )
    cookies.set_accept_policy(WebKit.CookieAcceptPolicy.ALWAYS)
    return session


def open_externally(widget: Gtk.Widget, uri: str) -> None:
    root = widget.get_root()
    launcher = Gtk.UriLauncher(uri=uri)
    launcher.launch(root if isinstance(root, Gtk.Window) else None, None, None)


class WebTab(Gtk.Overlay):
    """One web view plus its load-progress bar and error screen."""

    def __init__(self, session: WebKit.NetworkSession, title: str, url: str) -> None:
        super().__init__()
        self.title = title
        self.url = url
        # Set when the load never reached the server, which is worth retrying
        # once the network comes up. HTTP errors are not.
        self.failed = False
        self._loaded = False
        self._blocked = False

        self.view = WebKit.WebView(
            network_session=session,
            settings=WebKit.Settings(
                enable_developer_extras=True,
                enable_smooth_scrolling=True,
                media_playback_requires_user_gesture=True,
                enable_back_forward_navigation_gestures=True,
            ),
        )
        self.view.set_vexpand(True)
        self.view.set_background_color(draw.rgba(theme.BG_WINDOW))
        self.view.set_zoom_level(DEFAULT_ZOOM)
        self.set_child(self.view)

        self.progress = Gtk.ProgressBar(valign=Gtk.Align.START)
        self.progress.add_css_class("web-progress")
        self.progress.set_visible(False)
        self.add_overlay(self.progress)

        self.error = _ErrorScreen(on_retry=self.reload, on_open=self._load_origin)
        self.error.set_visible(False)
        self.add_overlay(self.error)

        self.view.connect("create", self._on_create)
        self.view.connect("load-changed", self._on_load_changed)
        self.view.connect("load-failed", self._on_load_failed)
        self.view.connect("decide-policy", self._on_decide_policy)
        self.view.connect("permission-request", self._on_permission_request)
        self.view.connect("notify::estimated-load-progress", self._on_progress)

    # -- loading ---------------------------------------------------------

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self._loaded = True
            self.load(self.url)

    def load(self, uri: str) -> None:
        self._blocked = False
        self.failed = False
        self.error.set_visible(False)
        self.view.load_uri(uri)

    def reload(self) -> None:
        self.load(self.view.get_uri() or self.url)

    def home(self) -> None:
        self.load(self.url)

    def _load_origin(self) -> None:
        parts = urlsplit(self.view.get_uri() or self.url)
        self.load(f"{parts.scheme}://{parts.netloc}/")

    @property
    def current_uri(self) -> str:
        return self.view.get_uri() or self.url

    # -- signals ---------------------------------------------------------

    def _on_progress(self, *_args) -> None:
        self.progress.set_fraction(self.view.get_estimated_load_progress())

    def _on_load_changed(self, _view, event: WebKit.LoadEvent) -> None:
        if event == WebKit.LoadEvent.STARTED:
            self.progress.set_fraction(0.0)
            self.progress.set_visible(True)
        elif event == WebKit.LoadEvent.COMMITTED:
            # Only a committed load means real content is on its way; blocked
            # and failed loads never get here, so their message stays up.
            self._blocked = False
            self.failed = False
            self.error.set_visible(False)
        elif event == WebKit.LoadEvent.FINISHED:
            self.progress.set_visible(False)

    def _on_load_failed(self, _view, _event, failing_uri: str,
                        error: GLib.Error) -> bool:
        self.progress.set_visible(False)
        # Our own decide-policy veto also lands here; keep the richer message.
        if self._blocked or error.matches(WebKit.NetworkError.quark(),
                                         WebKit.NetworkError.CANCELLED):
            return True
        self.failed = True
        self.error.show_failure(failing_uri, error.message)
        return True

    def _on_create(self, _view, action: WebKit.NavigationAction):
        """Refuse the popup window and follow it in this tab instead."""
        uri = action.get_request().get_uri()
        if uri:
            # WebKit is mid-decision; navigating has to wait for the unwind.
            GLib.idle_add(self.load, uri)
        return None

    def _on_decide_policy(self, _view, decision: WebKit.PolicyDecision,
                          kind: WebKit.PolicyDecisionType) -> bool:
        if kind == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
            # No popups in a half-height pane, and sending them to the system
            # browser would lose the session that sign-in flows rely on, so
            # they open in place instead. Alt+Left goes back.
            action = decision.get_navigation_action()
            decision.ignore()
            GLib.idle_add(self.load, action.get_request().get_uri())
            return True

        if kind != WebKit.PolicyDecisionType.RESPONSE:
            return False
        if not decision.is_main_frame_main_resource():
            return False

        response = decision.get_response()
        status = response.get_status_code()
        if status >= 400:
            self._blocked = True
            decision.ignore()
            self.progress.set_visible(False)
            self.error.show_status(status, response.get_uri())
            return True
        if not decision.is_mime_type_supported():
            # Downloads and other unrenderable content belong in a real browser.
            decision.ignore()
            open_externally(self, response.get_uri())
            return True
        return False

    @staticmethod
    def _on_permission_request(_view, request: WebKit.PermissionRequest) -> bool:
        request.deny()
        return True


class _ErrorScreen(Gtk.Box):
    """Full-pane message shown instead of a blank page."""

    def __init__(self, on_retry, on_open) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("web-error")
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_vexpand(True)
        inner.set_margin_start(24)
        inner.set_margin_end(24)

        self.code = Gtk.Label(label="", justify=Gtk.Justification.CENTER)
        self.code.add_css_class("web-error-code")
        self.headline = Gtk.Label(label="", justify=Gtk.Justification.CENTER)
        self.headline.add_css_class("metric-mid")
        self.headline.set_wrap(True)
        self.detail = Gtk.Label(label="", justify=Gtk.Justification.CENTER)
        self.detail.add_css_class("metric-sub")
        self.detail.set_wrap(True)
        self.detail.set_wrap_mode(2)  # PANGO_WRAP_WORD_CHAR
        for widget in (self.code, self.headline, self.detail):
            inner.append(widget)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        buttons.set_margin_top(6)
        self.open_button = Gtk.Button(label="Open sign-in page")
        self.open_button.connect("clicked", lambda *_: on_open())
        retry = Gtk.Button(label="Retry")
        retry.add_css_class("suggested-action")
        retry.connect("clicked", lambda *_: on_retry())
        buttons.append(self.open_button)
        buttons.append(retry)
        inner.append(buttons)
        self.append(inner)

    def show_status(self, status: int, uri: str) -> None:
        self.code.set_label(str(status))
        if status in AUTH_STATUSES:
            self.headline.set_label("Not signed in")
            self.detail.set_label(
                f"{uri}\n\nThe server refused the request. Open the sign-in page, "
                "log in, then retry — the session is shared by both tabs and kept "
                "between runs."
            )
        else:
            self.headline.set_label("The server returned an error")
            self.detail.set_label(uri)
        self.open_button.set_visible(status in AUTH_STATUSES)
        self.set_visible(True)

    def show_failure(self, uri: str, message: str) -> None:
        self.code.set_label("!")
        self.headline.set_label("Could not load the page")
        self.detail.set_label(f"{uri}\n\n{message}")
        self.open_button.set_visible(False)
        self.set_visible(True)


class WebPane(Gtk.Box):
    """Stack of web tabs; the switcher and controls live in the window header."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.session = make_session()
        self.zoom = DEFAULT_ZOOM

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=120)
        self.stack.set_vexpand(True)
        self.tabs: dict[str, WebTab] = {}
        for key, title, _icon, url in TABS:
            tab = WebTab(self.session, title, url)
            self.tabs[key] = tab
            self.stack.add_named(tab, key)
        self.append(self.stack)

        self.stack.connect("notify::visible-child-name", lambda *_: self._on_switch())
        self._on_switch()

        # Started at login the network is often not up yet; recover by itself
        # instead of leaving an error screen for the user to dismiss.
        self._network = Gio.NetworkMonitor.get_default()
        self._network.connect("network-changed", self._on_network_changed)

    # -- navigation ------------------------------------------------------

    @property
    def current(self) -> WebTab:
        name = self.stack.get_visible_child_name()
        return self.tabs[name] if name in self.tabs else next(iter(self.tabs.values()))

    def _on_switch(self) -> None:
        self.current.ensure_loaded()

    def _on_network_changed(self, _monitor, available: bool) -> None:
        if not available:
            return
        for tab in self.tabs.values():
            if tab.failed:
                tab.reload()

    def show_tab(self, key: str) -> None:
        if key in self.tabs:
            self.stack.set_visible_child_name(key)

    def build_switcher(self) -> Gtk.Widget:
        group = Adw.ToggleGroup()
        group.add_css_class("web-tabs")
        for key, title, icon, _url in TABS:
            group.add(Adw.Toggle(name=key, label=title, icon_name=icon))
        group.set_active_name(self.stack.get_visible_child_name() or TABS[0][0])
        group.connect("notify::active-name",
                      lambda g, _p: self.show_tab(g.get_active_name()))
        self.stack.connect(
            "notify::visible-child-name",
            lambda s, _p: group.set_active_name(s.get_visible_child_name()),
        )
        return group

    # -- actions ---------------------------------------------------------

    def reload(self) -> None:
        self.current.reload()

    def go_home(self) -> None:
        self.current.home()

    def go_back(self) -> None:
        view = self.current.view
        if view.can_go_back():
            view.go_back()

    def open_externally(self) -> None:
        open_externally(self, self.current.current_uri)

    def apply_theme(self) -> None:
        """Re-set the backdrop: WebKit stores the color, not the palette token.

        It shows through before a page paints and around short pages, so leaving
        it behind would flash the old theme on every load.
        """
        for tab in self.tabs.values():
            tab.view.set_background_color(draw.rgba(theme.BG_WINDOW))

    def apply_zoom(self, delta: float = 0.0, reset: bool = False) -> float:
        self.zoom = DEFAULT_ZOOM if reset else min(
            MAX_ZOOM, max(MIN_ZOOM, self.zoom + delta)
        )
        for tab in self.tabs.values():
            tab.view.set_zoom_level(self.zoom)
        return self.zoom
