#!/usr/bin/env python3
"""Generate the integration's brand images.

Since Home Assistant 2026.3 a custom integration ships its own branding in
a ``brand/`` directory and it takes priority over the brands CDN, so this
needs no pull request to home-assistant/brands and works on a private repo.

The source mark is CozyLife's own, carried over from the
``polaralias/homeassistant-cozylife`` integration's assets. It is CozyLife's
trademark, not ours and not that project's to license; using a vendor's mark
to identify an integration that talks to that vendor's devices is nominative
use and is what the whole Home Assistant brands catalogue does. See
"Branding" in the README.

The wordmark colour is sampled from the mark itself, so the lockup stays
consistent if the source art is ever replaced.

    python3 scripts/make_brand_assets.py
"""

from __future__ import annotations

import collections
import pathlib

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/assets/cozylife-mark-512.png"
OUT = ROOT / "custom_components/cozylife_cloud/brand"


def dominant_colour(img: Image.Image) -> tuple[int, int, int, int]:
    """The mark's background colour, used for the wordmark."""

    counts = collections.Counter(
        px for px in img.convert("RGBA").getdata() if px[3] > 200
    )
    (red, green, blue, _alpha), _count = counts.most_common(1)[0]
    return (red, green, blue, 255)


def wordmark_font(size: int) -> ImageFont.FreeTypeFont:
    """Prefer a geometric sans; fall back through what macOS actually has."""

    for path, index in (
        ("/System/Library/Fonts/Avenir Next.ttc", 2),
        ("/System/Library/Fonts/Avenir Next.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
        ("/System/Library/Fonts/Helvetica.ttc", 1),
    ):
        try:
            return ImageFont.truetype(path, size, index=index)
        except OSError:
            continue
    return ImageFont.load_default()


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mark = Image.open(SOURCE).convert("RGBA")

    # Icon: the mark alone, square.
    mark.resize((512, 512), Image.LANCZOS).save(OUT / "icon@2x.png")
    mark.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")

    # Logo: the mark plus a wordmark, for the wider slots in the UI.
    size = 1024
    icon = mark.resize((size, size), Image.LANCZOS)
    colour = dominant_colour(mark)

    font = wordmark_font(int(size * 0.52))
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), "CozyLife", font=font)
    text_w, text_h = right - left, bottom - top

    gap, pad = int(size * 0.18), int(size * 0.04)
    lockup = Image.new("RGBA", (size + gap + text_w + pad, size), (0, 0, 0, 0))
    lockup.alpha_composite(icon)
    ImageDraw.Draw(lockup).text(
        (size + gap - left, (size - text_h) / 2 - top),
        "CozyLife",
        font=font,
        fill=colour,
    )

    for name, height in (("logo@2x.png", 512), ("logo.png", 256)):
        width = round(lockup.width * height / lockup.height)
        lockup.resize((width, height), Image.LANCZOS).save(OUT / name)

    for path in sorted(OUT.iterdir()):
        with Image.open(path) as im:
            print(f"  {path.name:<16} {im.width}x{im.height}")


if __name__ == "__main__":
    build()
