"""The card's controls: what they look like, where they are, and how they change.

Split out of `overlay.py` following `card_glyphs.py`'s rule — these functions know how to
draw a button and nothing about what state the app is in. Which button should be lit, or
greyed out, arrives as a state name the window has already decided; no function here ever
asks the app anything.

Item ids go into the `Card` record the caller owns, so the window never holds a drawing
id of its own and a repaint cannot leave a stale one behind.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass

from voice_typer import card_glyphs
from voice_typer import card_layout as layout
from voice_typer import widget_theme as theme
from voice_typer.card_layout import Card, Metrics

Box = tuple[int, int, int, int]
Command = Callable[[], None]


@dataclass(frozen=True)
class Actions:
    """What the four main buttons do. Supplied by the window, which owns the app handle."""

    toggle_recording: Command
    toggle_pause: Command
    cancel_recording: Command
    quit: Command


def paint_row(canvas: tk.Canvas, m: Metrics, card: Card, actions: Actions) -> None:
    left, right = m.inner_edges(bleed=4)
    top, bottom = m.s(layout.BUTTON_TOP), m.s(layout.BUTTON_BOTTOM)
    square = bottom - top
    gap = m.s(8)

    flexible = right - left - gap * 3 - square * 2
    record_width = round(flexible * 1.15 / 2.15)

    record_box = (left, top, left + record_width, bottom)
    pause_box = (record_box[2] + gap, top, right - square * 2 - gap * 2, bottom)
    cancel_box = (pause_box[2] + gap, top, pause_box[2] + gap + square, bottom)
    power_box = (right - square, top, right, bottom)

    plain = (
        theme.BUTTON_TOP,
        theme.BUTTON_BOTTOM,
        theme.BUTTON_TOP_HOVER,
        theme.BUTTON_BOTTOM_HOVER,
    )
    card.buttons["record"] = layout.Button(record_box, actions.toggle_recording, *plain)
    card.buttons["pause"] = layout.Button(pause_box, actions.toggle_pause, *plain)
    card.buttons["cancel"] = layout.Button(
        cancel_box,
        actions.cancel_recording,
        theme.CANCEL_TOP,
        theme.CANCEL_BOTTOM,
        "#3d3327",
        "#1d1916",
    )
    card.buttons["power"] = layout.Button(
        power_box,
        actions.quit,
        theme.POWER_TOP,
        theme.POWER_BOTTOM,
        "#3d2a2c",
        "#1d1718",
    )

    radius = m.s(layout.BUTTON_RADIUS)
    borders = {"cancel": theme.CANCEL_BORDER, "power": theme.POWER_BORDER}
    # Only the four painted here. The fold control is already drawn, with its own
    # size and its own chevron, and painting it a second time would bury the chevron.
    for name in ("record", "pause", "cancel", "power"):
        button = card.buttons[name]
        button.fill_items = theme.rounded_gradient(
            canvas, button.box, radius, button.top, button.bottom
        )
        theme.rounded_outline(canvas, button.box, radius, borders.get(name, theme.BUTTON_BORDER))

    _paint_record_face(canvas, m, card, record_box)
    _paint_pause_face(canvas, m, card, pause_box)
    _paint_cross(canvas, m, card, cancel_box, theme.CANCEL_INK)
    _paint_power(canvas, m, power_box, theme.POWER_INK)


def _paint_record_face(canvas: tk.Canvas, m: Metrics, card: Card, box: Box) -> None:
    centre_y = (box[1] + box[3]) / 2
    icon_x = box[0] + m.s(30)
    card.record_icon = card_glyphs.draw_microphone(canvas, icon_x, centre_y, theme.ACCENT, m.c)
    card.record_label = canvas.create_text(
        icon_x + m.c(15),
        centre_y,
        text="ჩაწერა",
        anchor="w",
        fill=theme.TEXT_BRIGHT,
        font=m.font(theme.UI_FAMILY, theme.BUTTON_PX),
    )
    _centre_in(canvas, box, [*card.record_icon, card.record_label])


def _centre_in(canvas: tk.Canvas, box: Box, items: list[int]) -> None:
    """Slide a button's icon and label so the pair sits centred in it.

    Measured rather than computed: the label's width depends on the font Windows
    picked for Georgian, and it changes when the label does. Doing this after every
    text change is also what stops the group jumping sideways between states.
    """
    bounds = canvas.bbox(*items)
    if bounds is None:
        return
    wanted = (box[0] + box[2]) / 2
    current = (bounds[0] + bounds[2]) / 2
    shift = round(wanted - current)
    if shift:
        for item in items:
            canvas.move(item, shift, 0)


def _paint_pause_face(canvas: tk.Canvas, m: Metrics, card: Card, box: Box) -> None:
    centre_y = (box[1] + box[3]) / 2
    icon_x = box[0] + m.s(30)
    card.pause_bars = card_glyphs.draw_pause_bars(canvas, icon_x, centre_y, theme.TEXT_MUTED, m.c)
    card.pause_label = canvas.create_text(
        icon_x + m.c(15),
        centre_y,
        text="პაუზა",
        anchor="w",
        fill=theme.TEXT_BRIGHT,
        font=m.font(theme.UI_FAMILY, theme.BUTTON_PX),
    )
    _centre_in(canvas, box, [*card.pause_bars, card.pause_label])


def _paint_cross(canvas: tk.Canvas, m: Metrics, card: Card, box: Box, colour: str) -> None:
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    card.cancel_ink = card_glyphs.draw_cross(canvas, x, y, colour, m.c)


def _paint_power(canvas: tk.Canvas, m: Metrics, box: Box, colour: str) -> None:
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2 + m.s(0.5)
    card_glyphs.draw_power(canvas, x, y, colour, m.c)


def paint_mode(
    canvas: tk.Canvas, m: Metrics, card: Card, box: Box, *, rewrite: bool, command: Command
) -> None:
    """Which of the two things F9 does, shown as a word and switched by clicking it.

    Both shapes of the card paint this the same way, so there is exactly one place
    where the mode is drawn and exactly one where it is read.
    """
    box = tuple(round(edge) for edge in box)  # type: ignore[assignment]
    button = layout.Button(
        box,
        command,
        theme.BUTTON_TOP,
        theme.BUTTON_BOTTOM,
        theme.BUTTON_TOP_HOVER,
        theme.BUTTON_BOTTOM_HOVER,
    )
    radius = m.s(7)
    button.fill_items = theme.rounded_gradient(canvas, box, radius, button.top, button.bottom)
    theme.rounded_outline(canvas, box, radius, layout.ORANGE if rewrite else theme.BUTTON_BORDER)
    card.buttons["mode"] = button

    canvas.create_text(
        (box[0] + box[2]) / 2,
        (box[1] + box[3]) / 2,
        text="გამართვა" if rewrite else "სიტყვები",
        fill=layout.ORANGE if rewrite else theme.TEXT_MUTED,
        font=m.font(theme.UI_FAMILY, theme.FOOTER_PX + 1),
    )


def paint_fold(
    canvas: tk.Canvas, m: Metrics, card: Card, box: Box, *, pointing_up: bool, command: Command
) -> None:
    """The one control both shapes of the card have: fold away, or open back up."""
    box = tuple(round(edge) for edge in box)  # type: ignore[assignment]
    button = layout.Button(
        box,
        command,
        theme.BUTTON_TOP,
        theme.BUTTON_BOTTOM,
        theme.BUTTON_TOP_HOVER,
        theme.BUTTON_BOTTOM_HOVER,
    )
    radius = m.s(6)
    button.fill_items = theme.rounded_gradient(canvas, box, radius, button.top, button.bottom)
    theme.rounded_outline(canvas, box, radius, theme.BUTTON_BORDER)
    card.buttons["fold"] = button

    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    arm = m.c(3.8)
    rise = m.c(2.2)
    stroke = max(1, m.c(1.5))
    tip_y = y - rise if pointing_up else y + rise
    base_y = y + rise if pointing_up else y - rise
    for side in (-arm, arm):
        canvas.create_line(x + side, base_y, x, tip_y, fill=theme.TEXT_MUTED, width=stroke)


def at(card: Card, x: int, y: int) -> str | None:
    for name, button in card.buttons.items():
        if button.enabled and button.contains(x, y):
            return name
    return None


def set_hover(canvas: tk.Canvas, card: Card, name: str | None) -> None:
    for key, button in card.buttons.items():
        wanted = key == name
        if wanted == button.hovered:
            continue
        button.hovered = wanted
        top = button.top_hover if wanted else button.top
        bottom = button.bottom_hover if wanted else button.bottom
        theme.recolour_gradient(canvas, button.fill_items, top, bottom)
    canvas.config(cursor="hand2" if name else "")


def update(canvas: tk.Canvas, m: Metrics, card: Card, state: str, look: layout.Look) -> None:
    busy = state in ("recording", "paused")

    _set_enabled(canvas, m, card, "record", state not in ("transcribing", "disabled"))
    _set_enabled(canvas, m, card, "pause", busy)
    _set_enabled(canvas, m, card, "cancel", busy)

    _set_label(canvas, card, card.record_label, "გაჩერება" if busy else "ჩაწერა", "record")
    _set_label(
        canvas, card, card.pause_label, "გაგრძელება" if state == "paused" else "პაუზა", "pause"
    )

    # A bright cyan microphone beside a greyed-out label reads as a live button.
    ink = look.mic if card.buttons["record"].enabled else theme.DISABLED_INK
    for index, item in enumerate(card.record_icon):
        # The oval and the arc take `outline`; the stem is a line and takes `fill`.
        option = "outline" if index < 2 else "fill"
        canvas.itemconfig(item, **{option: ink})


def _set_label(canvas: tk.Canvas, card: Card, item: int, text: str, button: str) -> None:
    """Change a button's caption and re-centre its contents around the new width."""
    if canvas.itemcget(item, "text") == text:
        return
    canvas.itemconfig(item, text=text)
    group = card.record_icon if button == "record" else card.pause_bars
    _centre_in(canvas, card.buttons[button].box, [*group, item])


def _set_enabled(canvas: tk.Canvas, m: Metrics, card: Card, name: str, enabled: bool) -> None:
    button = card.buttons[name]
    if button.enabled == enabled:
        return
    button.enabled = enabled

    if not enabled and button.hovered:
        # Otherwise a button that switches off under the pointer keeps its lit
        # gradient and its hand cursor, and the next click there drags the window.
        button.hovered = False
        theme.recolour_gradient(canvas, button.fill_items, button.top, button.bottom)
        canvas.config(cursor="")

    if name == "record":
        canvas.itemconfig(
            card.record_label, fill=theme.TEXT_BRIGHT if enabled else theme.DISABLED_INK
        )
    elif name == "pause":
        ink = theme.TEXT_BRIGHT if enabled else theme.DISABLED_INK
        canvas.itemconfig(card.pause_label, fill=ink)
        for bar in card.pause_bars:
            canvas.itemconfig(bar, fill=theme.TEXT_MUTED if enabled else theme.DISABLED_INK)
    elif name == "cancel":
        for item in card.cancel_ink:
            canvas.itemconfig(item, fill=theme.CANCEL_INK if enabled else theme.DISABLED_INK)
