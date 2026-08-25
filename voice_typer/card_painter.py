"""Drawing the card: its shell, the status row, the level meter and the footer line.

Split out of `overlay.py` for the reason `card_glyphs.py` gives: these functions know how
to draw, and nothing about what state the app is in. Everything they need arrives as a
plain value the window has already worked out — including the microphone level, so no
function here ever asks the app a question mid-paint.

`paint` returns a `Card`: every id the canvas handed out, in one record. The window holds
that record and replaces it whole whenever the canvas is cleared, which is what makes a
stale drawing id — a silent no-op in Tk, and the reason a card can appear frozen —
impossible to leave behind.

The controls themselves live in `card_buttons`, which this module calls and which never
calls back.
"""

from __future__ import annotations

import tkinter as tk

from voice_typer import card_buttons
from voice_typer import card_layout as layout
from voice_typer import widget_theme as theme
from voice_typer.card_buttons import Actions
from voice_typer.card_layout import Card, Metrics


def paint(canvas: tk.Canvas, m: Metrics, actions: Actions, *, folded: bool, rewrite: bool) -> Card:
    """Draw the whole card and hand back everything it made.

    The record comes out of here rather than being filled in from outside, so the only
    way to hold a drawing is to have painted it — there is no moment in which the window
    holds ids for a card that is no longer on the canvas.
    """
    card = Card(folded=folded)
    if folded:
        _paint_collapsed_card(canvas, m, card, actions, rewrite=rewrite)
        return card

    margin = m.s(layout.CARD_MARGIN)
    box = (
        margin,
        margin,
        m.s(layout.WINDOW_WIDTH) - margin,
        m.s(layout.WINDOW_HEIGHT) - margin,
    )
    radius = m.s(layout.CARD_RADIUS)
    theme.rounded_gradient(canvas, box, radius, theme.CARD_TOP, theme.CARD_BOTTOM)
    theme.rounded_outline(canvas, box, radius, theme.CARD_BORDER)

    _paint_status_row(canvas, m, card, actions)
    _paint_meter(canvas, m, card)
    card_buttons.paint_row(canvas, m, card, actions)
    _paint_footer(canvas, m, card, actions, rewrite=rewrite)
    return card


def _paint_collapsed_card(
    canvas: tk.Canvas, m: Metrics, card: Card, actions: Actions, *, rewrite: bool
) -> None:
    """The folded strip: the state light, the running time, and the way back.

    Deliberately not a smaller copy of the card. Everything that was left out is
    something the user cannot act on without looking — and someone who folded the
    window away is not looking at it.
    """
    margin = m.s(layout.CARD_MARGIN)
    box = (
        margin,
        margin,
        m.s(m.card_width(folded=True, rewrite=rewrite)) - margin,
        m.s(layout.COLLAPSED_HEIGHT) - margin,
    )
    radius = m.s(layout.COLLAPSED_RADIUS)
    theme.rounded_gradient(canvas, box, radius, theme.CARD_TOP, theme.CARD_BOTTOM)
    theme.rounded_outline(canvas, box, radius, theme.CARD_BORDER)

    y = round((box[1] + box[3]) / 2)  # a gradient is painted row by row: whole pixels
    left = box[0] + m.s(layout.COLLAPSED_PAD)
    right = box[2] - m.s(layout.COLLAPSED_PAD)

    card.dot_items = theme.glow_dot(canvas, left + m.c(5), y, m.c(5), theme.ACCENT, theme.CARD_TOP)
    toggle = (right - m.c(20), y - m.c(10), right, y + m.c(10))
    card_buttons.paint_fold(
        canvas,
        m,
        card,
        toggle,
        pointing_up=True,
        command=actions.toggle_collapsed,
    )

    edge = toggle[0] - m.s(10)
    if rewrite:
        # Only in the mode that changes what gets pasted. The ordinary mode says
        # nothing, so anything the folded strip does say is worth reading.
        pill = (
            edge - m.s(layout.MODE_PILL_WIDTH * 0.72),
            y - m.c(9),
            edge,
            y + m.c(9),
        )
        card_buttons.paint_mode(
            canvas,
            m,
            card,
            pill,
            rewrite=rewrite,
            command=actions.toggle_rewrite_mode,
        )
        edge = pill[0] - m.s(8)

    card.timer_text = canvas.create_text(
        edge,
        y,
        text="0:00",
        anchor="e",
        fill=theme.ACCENT,
        font=m.font(theme.MONO_FAMILY, theme.MONO_PX),
    )


def _paint_status_row(canvas: tk.Canvas, m: Metrics, card: Card, actions: Actions) -> None:
    left, right = m.inner_edges()
    y = m.s(layout.STATUS_BASELINE)

    card.dot_items = theme.glow_dot(canvas, left + m.c(5), y, m.c(5), theme.ACCENT, theme.CARD_TOP)
    card.status_text = canvas.create_text(
        left + m.c(22),
        y,
        text="",
        anchor="w",
        fill=theme.TEXT_BRIGHT,
        font=m.font(theme.UI_FAMILY, theme.STATUS_PX),
    )

    # The fold control sits in the corner rather than in the button row: that row is
    # for what to do with a recording, and folding the window is not one of those.
    fold = (right - m.c(20), y - m.c(10), right, y + m.c(10))
    card_buttons.paint_fold(
        canvas,
        m,
        card,
        fold,
        pointing_up=False,
        command=actions.toggle_collapsed,
    )

    # The badge is sized with the lettering inside it rather than with the card, or
    # a larger "F9" would push against its own border.
    badge_right = fold[0] - m.s(10)
    badge = (badge_right - m.c(34), y - m.c(10), badge_right, y + m.c(10))
    theme.rounded_gradient(canvas, badge, m.c(5), "#26292c", "#1a1d20")
    theme.rounded_outline(canvas, badge, m.c(5), "#3a3e43")
    card.badge_text = canvas.create_text(
        (badge[0] + badge[2]) / 2,
        y,
        text="F9",
        fill=theme.TEXT_MUTED,
        font=m.font(theme.MONO_FAMILY, theme.BADGE_PX),
    )

    # Measured from the badge, not from the card's edge: the badge is what the timer
    # would collide with, and it is the thing whose width changes.
    card.timer_text = canvas.create_text(
        badge[0] - m.s(12),
        y,
        text="0:00",
        anchor="e",
        fill=theme.ACCENT,
        font=m.font(theme.MONO_FAMILY, theme.MONO_PX),
    )


def _paint_meter(canvas: tk.Canvas, m: Metrics, card: Card) -> None:
    left, right = m.inner_edges(bleed=4)
    middle = m.s(layout.METER_MIDDLE)

    canvas.create_line(
        left, middle, right, middle, fill=theme.blend(theme.ACCENT, theme.CARD_TOP, 0.35)
    )

    gap = m.s(layout.BAR_GAP)
    span = (right - left - gap * (layout.BAR_COUNT - 1)) / layout.BAR_COUNT
    for index in range(layout.BAR_COUNT):
        x = left + index * (span + gap)
        card.bars.append(
            canvas.create_rectangle(
                x,
                middle - 0.5,
                x + span,
                middle + 0.5,
                fill=theme.blend(theme.ACCENT, theme.CARD_TOP, 0.5),
                width=0,
            )
        )


def fit_text(canvas: tk.Canvas, item: int, text: str, room: int) -> None:
    """Put text on an item, trimmed with an ellipsis until it fits.

    Measured rather than counted, for the reason `_centre_in` already gives: how
    wide a Georgian string comes out depends on the font Windows picked for it, so
    a character budget is a guess and `bbox` is an answer.
    """
    canvas.itemconfig(item, text=text)
    if not text:
        return
    while len(text) > 1:
        bounds = canvas.bbox(item)
        if bounds is None or bounds[2] - bounds[0] <= room:
            return
        text = text[:-2] + "…"
        canvas.itemconfig(item, text=text)


def _paint_footer(
    canvas: tk.Canvas, m: Metrics, card: Card, actions: Actions, *, rewrite: bool
) -> None:
    left, right = m.inner_edges(bleed=4)
    y = m.s(layout.FOOTER_BASELINE)
    font = m.font(theme.MONO_FAMILY, theme.FOOTER_PX)
    # The mode lives here rather than in the button row: that row is for what to do
    # with a recording, and this decides what happens to the words afterwards. It also
    # has to be readable at a glance, which the footer line is and a fifth square
    # button next to four others would not be.
    pill = (
        left,
        y - m.s(layout.MODE_PILL_HEIGHT / 2),
        left + m.s(layout.MODE_PILL_WIDTH),
        y + m.s(layout.MODE_PILL_HEIGHT / 2),
    )
    card_buttons.paint_mode(
        canvas,
        m,
        card,
        pill,
        rewrite=rewrite,
        command=actions.toggle_rewrite_mode,
    )
    # Kept short on purpose: Consolas has no Georgian, so Tk substitutes a wider font
    # for those runs and a longer line collides with the mode pill on the left.
    card.shown_notice = ""  # so a re-fit only happens when the message changes
    card.device_text = canvas.create_text(
        right, y, text="", anchor="e", fill=theme.TEXT_FAINT, font=font
    )
    # What the app had to say, in the space the microphone name usually occupies. It
    # goes here rather than on the status row because an overlapping take can bring a
    # message in while the next recording is already running, and hiding "იწერს" to
    # show it would make the card lie about what it is doing. In its own family, not
    # the footer's monospace: that font has no Georgian at all.
    card.notice_text = canvas.create_text(
        left + m.s(layout.MODE_PILL_WIDTH) + m.s(layout.NOTICE_GAP),
        y,
        text="",
        anchor="w",
        fill=layout.AMBER,
        font=m.font(theme.UI_FAMILY, theme.FOOTER_PX),
    )
    card.notice_room = right - m.s(layout.MODE_PILL_WIDTH) - m.s(layout.NOTICE_GAP) - left


def show_meter(canvas: tk.Canvas, m: Metrics, card: Card, *, level: float, colour: str) -> None:
    """Scroll the level history leftwards, newest at the right — a recorder's trace.

    The level arrives already decided; whether the microphone is worth listening to in
    this state is the window's judgement, not the meter's. It is clamped again here
    because a NaN would silently draw nothing at all.
    """
    level = 0.0 if level != level else min(1.0, max(0.0, level))  # NaN reads as 0
    card.levels = [*card.levels[1:], level]

    # Fourteen times a second, redrawing 58 rectangles that are all already flat is
    # most of what this window costs while it sits there doing nothing.
    settled = not any(card.levels)
    if settled and card.meter_settled and colour == card.meter_colour:
        return
    card.meter_settled = settled
    card.meter_colour = colour

    middle = m.s(layout.METER_MIDDLE)
    tallest = m.s(layout.METER_HEIGHT) - m.s(2)
    floor = m.s(layout.BAR_MIN_HEIGHT)
    faded = theme.blend(colour, theme.CARD_TOP, 0.45)

    for index, bar in enumerate(card.bars):
        height = max(floor, card.levels[index] * tallest)
        x0, _, x1, _ = canvas.coords(bar)
        canvas.coords(bar, x0, middle - height / 2, x1, middle + height / 2)
        canvas.itemconfig(bar, fill=colour if height > floor else faded)
