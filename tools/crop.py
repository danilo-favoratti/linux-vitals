#!/usr/bin/env python3
"""Crop and optionally upscale a PNG region. Development helper.

Usage: python3 tools/crop.py in.png out.png X Y W H [SCALE]
"""

import sys

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402


def main() -> int:
    src, dst, x, y, w, h = sys.argv[1:7]
    scale = float(sys.argv[7]) if len(sys.argv) > 7 else 1.0
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(src)
    x, y = max(0, int(x)), max(0, int(y))
    w = min(int(w), pixbuf.get_width() - x)
    h = min(int(h), pixbuf.get_height() - y)
    cropped = pixbuf.new_subpixbuf(x, y, w, h)
    if scale != 1.0:
        cropped = cropped.scale_simple(
            int(w * scale), int(h * scale), GdkPixbuf.InterpType.NEAREST
        )
    cropped.savev(dst, "png", [], [])
    print(f"saved {dst} ({cropped.get_width()}x{cropped.get_height()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
