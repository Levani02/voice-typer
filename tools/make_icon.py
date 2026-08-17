"""Draw `assets/voice-typer.ico` — the widget's dark card with its teal microphone.

Run it after changing the accent colour or the card's shading, so the desktop shortcut
keeps matching the window:

    .venv\\Scripts\\python.exe tools\\make_icon.py

It writes a multi-size .ico (16 to 256 px) plus a .png for looking at.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

# Run as a script from anywhere, and still find the package beside this folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_typer import widget_theme as theme

SIZE = 512
OUT = Path(__file__).resolve().parent.parent / "assets" / "voice-typer.ico"
ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]

BORDER = (*theme.to_rgb(theme.CARD_BORDER), 255)
ACCENT = (*theme.ACCENT_RGB, 255)


def _card() -> Image.Image:
    """A vertical gradient inside a rounded square — the window's own card, squared off."""
    top, bottom = theme.to_rgb(theme.CARD_TOP), theme.to_rgb(theme.CARD_BOTTOM)
    column = Image.new("RGBA", (1, SIZE))
    for y in range(SIZE):
        position = y / (SIZE - 1)
        shade = tuple(round(top[i] + (bottom[i] - top[i]) * position) for i in range(3))
        column.putpixel((0, y), (*shade, 255))

    card = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=SIZE // 5, fill=255)
    card.paste(column.resize((SIZE, SIZE)), (0, 0), mask)
    ImageDraw.Draw(card).rounded_rectangle(
        (2, 2, SIZE - 3, SIZE - 3), radius=SIZE // 5, outline=BORDER, width=max(2, SIZE // 120)
    )
    return card


def _microphone(card: Image.Image) -> None:
    """Capsule, cradle, stem — the record button's glyph at icon size."""
    draw = ImageDraw.Draw(card)
    centre_x, centre_y = SIZE / 2, SIZE / 2
    stroke = round(SIZE * 0.055)

    # Narrow enough to read as a capsule rather than a circle: at a square-ish ratio the
    # corner radius swallows the straight sides and the glyph turns into a face.
    half_width, height = SIZE * 0.098, SIZE * 0.34
    top = centre_y - SIZE * 0.30
    draw.rounded_rectangle(
        (centre_x - half_width, top, centre_x + half_width, top + height),
        radius=half_width,
        outline=ACCENT,
        width=stroke,
    )
    draw.arc(
        (
            centre_x - SIZE * 0.195,
            centre_y - SIZE * 0.17,
            centre_x + SIZE * 0.195,
            centre_y + SIZE * 0.21,
        ),
        start=20,
        end=160,
        fill=ACCENT,
        width=stroke,
    )
    draw.line(
        (centre_x, centre_y + SIZE * 0.19, centre_x, centre_y + SIZE * 0.325),
        fill=ACCENT,
        width=stroke,
    )


def main() -> None:
    card = _card()
    _microphone(card)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    card.save(OUT, sizes=ICO_SIZES)
    card.resize((128, 128), Image.LANCZOS).save(OUT.with_suffix(".png"))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
