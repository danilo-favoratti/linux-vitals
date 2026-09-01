"""Makes the GTK4 WebKit bindings importable.

Ubuntu ships the library (``libwebkitgtk-6.0``) and its introspection data
(``gir1.2-webkit-6.0``) in separate packages, and the second one is not pulled
in by the desktop. When it is missing, ``tools/setup_webkit.sh`` unpacks the
typelibs into ``vendor/`` without needing root and this module points
GObject-Introspection at them.

The search path is registered on the introspection repository itself rather
than through ``GI_TYPELIB_PATH``, which GI only reads once, when its default
repository is created - by then an importing script may already have called
``gi.require_version`` for something else.
"""

from __future__ import annotations

import glob
import os
import pathlib

# Vulkan on NVIDIA + a rotated Wayland output (the portrait desk panel) often
# produces a surface that never paints. ngl is GSK's GL renderer and actually
# shows a window on that setup. Must be set before GTK is initialised.
os.environ.setdefault("GSK_RENDERER", "ngl")

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR_TYPELIB_DIR = PROJECT_ROOT / "vendor" / "girepository-1.0"
TYPELIB = "WebKit-6.0.typelib"


def _installed_system_wide() -> bool:
    return any(
        glob.glob(f"/usr/lib*/girepository-1.0/{TYPELIB}")
        + glob.glob(f"/usr/lib/*/girepository-1.0/{TYPELIB}")
    )


def _repository():
    import gi

    for holder in (gi, getattr(gi, "_gi", None)):
        getter = getattr(getattr(holder, "Repository", None), "get_default", None)
        if getter is not None:
            return getter()
    return None


def add_vendor_typelibs() -> None:
    """Add ``vendor/`` to the typelib search path when the distro lacks it."""
    if _installed_system_wide() or not (VENDOR_TYPELIB_DIR / TYPELIB).exists():
        return

    repository = _repository()
    if repository is not None:
        repository.prepend_search_path(str(VENDOR_TYPELIB_DIR))

    # Child processes (WebKit's helpers, dev tools) get it through the
    # environment.
    existing = [p for p in os.environ.get("GI_TYPELIB_PATH", "").split(os.pathsep) if p]
    if str(VENDOR_TYPELIB_DIR) not in existing:
        os.environ["GI_TYPELIB_PATH"] = os.pathsep.join(
            [*existing, str(VENDOR_TYPELIB_DIR)]
        )


def webkit_available() -> bool:
    import gi

    try:
        gi.require_version("WebKit", "6.0")
    except ValueError:
        return False
    return True


MISSING_WEBKIT_HINT = (
    "WebKit for GTK4 is not available.\n\n"
    "Install the introspection data system-wide:\n"
    "    sudo apt install gir1.2-webkit-6.0\n\n"
    "or unpack it locally without root:\n"
    "    ./tools/setup_webkit.sh"
)
