#!/usr/bin/env python3
"""Turn assets/icon-master.png into the icon sizes the desktop needs.

The master image is a square with opaque corners; desktop icons want the area
outside the rounded badge to be transparent. The mask is drawn oversampled and
then downscaled so its edge is smooth.

Usage: python3 tools/make_icon.py
"""

from __future__ import annotations

import pathlib
import sys

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parent.parent
MASTER = ROOT / "assets" / "icon-master.png"
OUT_DIR = ROOT / "assets" / "icons"
ICON_NAME = "dev.favoratti.Vitals"

SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
# Fractions of the master's width, measured against the badge in the artwork.
INSET = 0.008
RADIUS = 0.20
SUPERSAMPLE = 4


def rounded_mask(size: int) -> Image.Image:
    big = size * SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    inset = INSET * big
    ImageDraw.Draw(mask).rounded_rectangle(
        (inset, inset, big - 1 - inset, big - 1 - inset),
        radius=RADIUS * big,
        fill=255,
    )
    return mask.resize((size, size), Image.LANCZOS)


def main() -> int:
    if not MASTER.exists():
        print(f"missing {MASTER}", file=sys.stderr)
        return 1

    master = Image.open(MASTER).convert("RGBA")
    master.putalpha(rounded_mask(master.width))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for size in SIZES:
        icon = master.resize((size, size), Image.LANCZOS)
        path = OUT_DIR / f"{ICON_NAME}-{size}.png"
        icon.save(path, optimize=True)
        print(f"wrote {path.relative_to(ROOT)}")

    preview = ROOT / "docs" / "icon.png"
    master.resize((256, 256), Image.LANCZOS).save(preview, optimize=True)
    print(f"wrote {preview.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
