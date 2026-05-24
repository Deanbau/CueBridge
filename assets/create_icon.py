"""
Generate CueBridge app icon (PNG + ICO).
Run: python assets/create_icon.py
Requires: pillow
"""

import math
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("pip install pillow")

OUT = Path(__file__).parent
SIZE = 1024

BG      = (20,  20,  46,  255)
ORANGE  = (245, 158, 11,  255)
ORANGE2 = (180, 110,  5,  255)


def make_icon(size: int) -> Image.Image:
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s    = size / SIZE  # scale factor

    # Rounded background
    pad = int(40 * s)
    r   = int(160 * s)
    draw.rounded_rectangle([pad, pad, size - pad, size - pad], radius=r, fill=BG)

    # ── Bridge ──────────────────────────────────────────────────────────────
    tw   = int(56 * s)   # tower width
    th   = int(320 * s)  # tower height
    ty   = int(430 * s)  # tower top y
    lx   = int(220 * s)  # left tower x
    rx   = size - lx - tw  # right tower x

    # Towers
    draw.rectangle([lx, ty, lx + tw, ty + th], fill=ORANGE)
    draw.rectangle([rx, ty, rx + tw, ty + th], fill=ORANGE)

    # Arch (suspension arc between tower tops)
    cx   = size / 2
    cy   = ty
    rx_a = (rx - lx) / 2
    ry_a = int(220 * s)
    line_w = max(2, int(22 * s))
    pts  = []
    for i in range(121):
        angle = math.pi + math.pi * i / 120
        pts.append((cx + rx_a * math.cos(angle), cy + ry_a * math.sin(angle)))
    draw.line(pts, fill=ORANGE, width=line_w)

    # Deck
    deck_y = ty + int(200 * s)
    deck_h = max(1, int(28 * s))
    draw.rectangle([lx, deck_y, rx + tw, deck_y + deck_h], fill=ORANGE)

    # Suspenders (vertical cables arch → deck)
    susp_w = max(1, int(8 * s))
    for i in range(1, 5):
        t     = i / 5
        angle = math.pi + math.pi * t
        ax    = cx + rx_a * math.cos(angle)
        ay    = cy + ry_a * math.sin(angle)
        dx    = lx + tw // 2 + (rx - lx) * t
        draw.line([(ax, ay), (dx, deck_y)], fill=ORANGE2, width=susp_w)

    return img


def main():
    icon = make_icon(SIZE)
    icon.save(OUT / "icon.png")
    print(f"Saved {OUT / 'icon.png'}")

    # ICO (Windows) — multiple sizes in one file
    sizes = [(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]
    frames = [make_icon(s[0]).resize((s[0], s[0]), Image.LANCZOS) for s in sizes]
    frames[0].save(OUT / "icon.ico", format="ICO", append_images=frames[1:],
                   sizes=sizes)
    print(f"Saved {OUT / 'icon.ico'}")


def make_launcher_icon():
    img = make_icon(80)
    img.save(OUT / "icon_launcher.png")
    print(f"Saved {OUT / 'icon_launcher.png'}")


if __name__ == "__main__":
    main()
    make_launcher_icon()
