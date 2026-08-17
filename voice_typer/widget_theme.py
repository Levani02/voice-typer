"""Colours, sizes and drawing helpers for the recorder window.

Tk has no gradients, no rounded corners, no shadows and no alpha. The design being
matched here has all four, so every one of them is drawn by hand on a Canvas: gradients
as one line per row, rounded corners by insetting each row to follow the arc, and
translucency by blending against the colour underneath before drawing.

Keeping that arithmetic here leaves `overlay.py` to describe the layout rather than the
mechanics.
"""

from __future__ import annotations

import math
import tkinter as tk

Colour = str
Rgb = tuple[int, int, int]

# The window is a rectangle; this colour is punched out of it so the card's rounded
# corners show whatever is behind the window instead of a black box.
TRANSPARENT_KEY = "#010203"

ACCENT: Colour = "#7ee2de"
ACCENT_RGB: Rgb = (126, 226, 222)

CARD_TOP: Colour = "#1c1e20"
CARD_BOTTOM: Colour = "#0e0f11"
CARD_BORDER: Colour = "#3c4045"

TEXT_BRIGHT: Colour = "#f0f4f6"
TEXT_MUTED: Colour = "#8d9ba1"
TEXT_FAINT: Colour = "#5d686d"

BUTTON_TOP: Colour = "#383c40"
BUTTON_BOTTOM: Colour = "#181a1d"
BUTTON_BORDER: Colour = "#3f4348"
BUTTON_TOP_HOVER: Colour = "#4a4f54"
BUTTON_BOTTOM_HOVER: Colour = "#232629"

CANCEL_TOP: Colour = "#302820"
CANCEL_BOTTOM: Colour = "#161311"
CANCEL_BORDER: Colour = "#6b543c"
CANCEL_INK: Colour = "#e9a75f"

POWER_TOP: Colour = "#302022"
POWER_BOTTOM: Colour = "#161112"
POWER_BORDER: Colour = "#6d3c3e"
POWER_INK: Colour = "#f08787"

DISABLED_INK: Colour = "#4e565a"

# Georgian needs a font that actually has the Mkhedruli block. Segoe UI has carried it
# since Windows 10 1903.
UI_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"

# Heights in pixels at 100% display scaling. `overlay.py` multiplies these by the real
# scaling factor and passes them to Tk as negative sizes, which means pixels rather than
# points — the only way to keep the design's proportions exact on any display.
STATUS_PX = 17
MONO_PX = 17
BUTTON_PX = 15
BADGE_PX = 11
FOOTER_PX = 11


def to_rgb(colour: Colour) -> Rgb:
    return (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))


def to_hex(rgb: Rgb) -> Colour:
    red, green, blue = (max(0, min(255, round(value))) for value in rgb)
    return f"#{red:02x}{green:02x}{blue:02x}"


def blend(front: Colour, back: Colour, alpha: float) -> Colour:
    """`front` at `alpha` over `back`. Tk cannot do translucency, so it is precomputed."""
    a, b = to_rgb(front), to_rgb(back)
    return to_hex(tuple(a[i] * alpha + b[i] * (1 - alpha) for i in range(3)))


def mix(start: Colour, end: Colour, position: float) -> Colour:
    """A point along a gradient, 0 at `start` and 1 at `end`."""
    a, b = to_rgb(start), to_rgb(end)
    return to_hex(tuple(a[i] + (b[i] - a[i]) * position for i in range(3)))


def _row_inset(row: int, height: int, radius: int) -> float:
    """How far in this row starts, so the stack of rows forms a rounded rectangle."""
    if row < radius:
        offset = radius - row - 0.5
    elif row >= height - radius:
        offset = row - (height - radius) + 0.5
    else:
        return 0.0
    return radius - math.sqrt(max(0.0, radius * radius - offset * offset))


def rounded_gradient(
    canvas: tk.Canvas,
    box: tuple[int, int, int, int],
    radius: int,
    top: Colour,
    bottom: Colour,
) -> list[int]:
    """Fill a rounded rectangle with a vertical gradient, one canvas line per row.

    Returns the line ids so the same shape can be recoloured later — that is how the
    buttons light up under the pointer without being rebuilt.
    """
    x0, y0, x1, y1 = box
    height = y1 - y0
    items: list[int] = []
    for row in range(height):
        inset = _row_inset(row, height, radius)
        colour = mix(top, bottom, row / max(1, height - 1))
        items.append(
            canvas.create_line(
                x0 + inset, y0 + row + 0.5, x1 - inset, y0 + row + 0.5, fill=colour, width=1
            )
        )
    return items


def recolour_gradient(canvas: tk.Canvas, items: list[int], top: Colour, bottom: Colour) -> None:
    """Repaint a shape built by `rounded_gradient` without rebuilding it."""
    last = max(1, len(items) - 1)
    for row, item in enumerate(items):
        canvas.itemconfig(item, fill=mix(top, bottom, row / last))


def rounded_outline(
    canvas: tk.Canvas, box: tuple[int, int, int, int], radius: int, colour: Colour
) -> list[int]:
    """A one-pixel rounded border, drawn as the outermost pixel of each row."""
    x0, y0, x1, y1 = box
    height = y1 - y0
    items: list[int] = []
    for row in range(height):
        inset = _row_inset(row, height, radius)
        near_end = row < radius or row >= height - radius
        y = y0 + row + 0.5
        if near_end:
            # On the curves, draw the whole row's edge pixels; elsewhere just the sides.
            items.append(canvas.create_line(x0 + inset, y, x0 + inset + 1, y, fill=colour))
            items.append(canvas.create_line(x1 - inset - 1, y, x1 - inset, y, fill=colour))
        else:
            items.append(canvas.create_line(x0, y, x0 + 1, y, fill=colour))
            items.append(canvas.create_line(x1 - 1, y, x1, y, fill=colour))

    # The caps start where the first row's own edge pixel already is, not at `radius`.
    # Starting at `radius` leaves an unpainted notch of `radius - inset - 1` pixels in
    # every corner — four pixels wide on the card, and visible.
    cap = x0 + _row_inset(0, height, radius) + 1
    items.append(canvas.create_line(cap, y0 + 0.5, x1 - (cap - x0), y0 + 0.5, fill=colour))
    items.append(canvas.create_line(cap, y1 - 0.5, x1 - (cap - x0), y1 - 0.5, fill=colour))
    return items


# Ring size and opacity for the halo around the status dot, outermost first. Recolouring
# has to walk the same ladder, or the outer ring gains an edge instead of fading out.
GLOW_RINGS = ((3.0, 0.10), (2.1, 0.18), (1.45, 0.30))


def recolour_glow_dot(canvas: tk.Canvas, items: list[int], colour: Colour, behind: Colour) -> None:
    """Repaint a dot built by `glow_dot`, keeping the halo graded."""
    for item, (_step, alpha) in zip(items, GLOW_RINGS, strict=False):
        canvas.itemconfig(item, fill=blend(colour, behind, alpha))
    canvas.itemconfig(items[-1], fill=colour)


def glow_dot(
    canvas: tk.Canvas, x: float, y: float, radius: float, colour: Colour, behind: Colour
) -> list[int]:
    """A filled circle with a soft halo, faked as rings blended against the card."""
    items: list[int] = []
    for step, alpha in GLOW_RINGS:
        ring = radius * step
        items.append(
            canvas.create_oval(
                x - ring,
                y - ring,
                x + ring,
                y + ring,
                fill=blend(colour, behind, alpha),
                width=0,
            )
        )
    items.append(
        canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=colour, width=0)
    )
    return items
