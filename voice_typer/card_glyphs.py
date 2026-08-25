"""The shapes drawn on the card's buttons, as plain functions.

`widget_theme.py` already says why this kind of code moves out of `overlay.py`: keeping
the arithmetic elsewhere "leaves `overlay.py` to describe the layout rather than the
mechanics". Colours and gradients made that move; the glyphs did not, and the file grew
past twice its limit carrying them.

Every function here takes the canvas, a position, a colour and a scale, and returns the
ids it created so the caller can recolour or move them later. None of them knows what a
button is, what state the app is in, or which glyph means what — that stays in the
overlay, which is the part worth reading to understand the card.

`scale` is a callable rather than a number because the overlay draws glyphs against its
*content* scale and positions against its *layout* scale, and only the caller knows which
is which.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

Scale = Callable[[float], int]


def draw_microphone(canvas: tk.Canvas, x: float, y: float, colour: str, c: Scale) -> list[int]:
    """A microphone: capsule body, the cradle under it, and the stem.

    Sized against the button rather than copied from the design's own pixels — the card
    is 58% of the design's width, so the design's 20px glyph would read as twice the
    weight beside a label that did scale down.
    """
    stroke = max(1, c(1.4))
    return [
        canvas.create_oval(
            x - c(2.4), y - c(6), x + c(2.4), y + c(0.5), outline=colour, width=stroke
        ),
        canvas.create_arc(
            x - c(4.6),
            y - c(4),
            x + c(4.6),
            y + c(4.4),
            start=200,
            extent=140,
            style="arc",
            outline=colour,
            width=stroke,
        ),
        canvas.create_line(x, y + c(4.4), x, y + c(6.6), fill=colour, width=stroke),
    ]


def draw_pause_bars(canvas: tk.Canvas, x: float, y: float, colour: str, c: Scale) -> list[int]:
    """The two upright bars of a pause button."""
    return [
        canvas.create_rectangle(
            x + c(offset),
            y - c(5.5),
            x + c(offset + 2.4),
            y + c(5.5),
            fill=colour,
            width=0,
        )
        for offset in (0, 4.8)
    ]


def draw_cross(canvas: tk.Canvas, x: float, y: float, colour: str, c: Scale) -> list[int]:
    """The ✕ that throws a take away."""
    arm = c(4)
    stroke = max(1, c(1.6))
    return [
        canvas.create_line(x - arm, y - arm, x + arm, y + arm, fill=colour, width=stroke),
        canvas.create_line(x + arm, y - arm, x - arm, y + arm, fill=colour, width=stroke),
    ]


def draw_power(canvas: tk.Canvas, x: float, y: float, colour: str, c: Scale) -> list[int]:
    """The standard power glyph: a ring open at the top, with a stroke through the gap.

    Tk measures arc angles anticlockwise from three o'clock, so a gap centred on twelve
    o'clock means starting past it and sweeping the rest of the way round.
    """
    ring = c(5.2)
    stroke = max(1, c(1.6))
    return [
        canvas.create_arc(
            x - ring,
            y - ring,
            x + ring,
            y + ring,
            start=125,
            extent=290,
            style="arc",
            outline=colour,
            width=stroke,
        ),
        canvas.create_line(x, y - c(7.4), x, y - c(1.4), fill=colour, width=stroke),
    ]
