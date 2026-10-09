"""Generate the launcher's app icon.

Kept as a script rather than only committing the .ico so the icon is
reproducible and tweakable — a binary blob nobody can regenerate is the
kind of thing that rots.

The mark is the product, drawn literally: the top bar (the character
persona) stays full width and bright; the bars below it (older chat
history) get progressively shorter and dimmer. That reads at 16px, which
rules out anything with fine detail or text.

    python make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT_ICO = HERE / "app.ico"
OUT_PNG = HERE / "app_256.png"
# Tk's PhotoImage can't scale, so the in-window header icon has to exist at
# exactly the size the header asks for.
OUT_HEADER = HERE / "app_header.png"
HEADER_SIZE = 44

# Violet accent — reads well on both light and dark taskbars, and matches
# the audience this is aimed at (roleplay / character-chat users) better
# than a generic dev-tool blue.
BG_TOP = (124, 92, 246)      # #7C5CF6
BG_BOTTOM = (167, 139, 250)  # #A78BFA
BAR_BRIGHT = (255, 255, 255)
BAR_DIM = (255, 255, 255)

SIZES = [16, 24, 32, 48, 64, 128, 256]
SUPERSAMPLE = 4  # draw big, downscale — Pillow has no built-in AA for shapes


def _rounded_rect(draw: ImageDraw.ImageDraw, box, radius, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def render(size: int) -> Image.Image:
    s = size * SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # --- rounded tile with a vertical gradient -----------------------------
    grad = Image.new("RGBA", (1, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / max(1, s - 1)
        gd.point(
            (0, y),
            fill=(
                int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t),
                int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t),
                int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t),
                255,
            ),
        )
    grad = grad.resize((s, s))

    mask = Image.new("L", (s, s), 0)
    _rounded_rect(ImageDraw.Draw(mask), (0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    # --- the bars ----------------------------------------------------------
    d = ImageDraw.Draw(img)
    left = s * 0.20
    bar_h = s * 0.085
    radius = bar_h / 2
    # (width fraction, alpha) — top bar full + opaque (persona, untouched),
    # the rest shorter + fading (older history, compressed away)
    # Alphas deliberately don't fade all the way out: at 16px the faintest
    # bar disappeared entirely and the staircase silhouette lost its last
    # step, so the floor is 130, not ~95.
    bars = [
        (0.60, 255),
        (0.46, 205),
        (0.34, 165),
        (0.24, 130),
    ]
    y = s * 0.24
    gap = s * 0.135
    for i, (w_frac, alpha) in enumerate(bars):
        color = BAR_BRIGHT if i == 0 else BAR_DIM
        _rounded_rect(
            d, (left, y, left + s * w_frac, y + bar_h), radius, (*color, alpha)
        )
        y += gap

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    frames = [render(sz) for sz in SIZES]
    frames[-1].save(OUT_PNG)
    # Pillow writes every requested size into one .ico
    frames[-1].save(OUT_ICO, format="ICO", sizes=[(sz, sz) for sz in SIZES])
    render(HEADER_SIZE).save(OUT_HEADER)
    print(f"wrote {OUT_ICO} ({OUT_ICO.stat().st_size} bytes) with sizes {SIZES}")
    print(f"wrote {OUT_PNG}")
    print(f"wrote {OUT_HEADER} ({HEADER_SIZE}px, for the in-window header)")


if __name__ == "__main__":
    main()
