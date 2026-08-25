"""The card's measurements and colours — everything that can be decided without drawing.

Split out of `overlay.py` for the reason `widget_theme.py` states about itself: keeping
the arithmetic here leaves the window to describe the layout rather than the mechanics.
Nothing in this file imports tkinter, touches a canvas, or knows what state the app is
in, so all of it can be checked on a machine with no screen.

**Every measurement here is in design pixels at 100% scaling.** `Metrics` turns them into
real screen pixels. Drawing at a fixed size and letting the system stretch the result is
what makes an overlay look soft and chunky on a scaled display.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from voice_typer import widget_theme as theme

REFRESH_MS = 70  # fast enough for the level bars to look alive

WINDOW_WIDTH = 520
WINDOW_HEIGHT = 176
# Collapsed, the card keeps only what someone glances at while dictating: is it listening,
# and for how long. Every control goes away — the hotkey does not, which is the whole
# point of being allowed to fold the window out of the way.
COLLAPSED_WIDTH = 196
COLLAPSED_HEIGHT = 56
COLLAPSED_RADIUS = 14
COLLAPSED_PAD = 16
# Folded, the strip grows only in the mode that has something to announce. Rewrite mode
# changes what lands at the cursor, so it is never allowed to hide behind a folded card.
COLLAPSED_MODE_EXTRA = 104
MODE_PILL_WIDTH = 132
MODE_PILL_HEIGHT = 19

# Clear air between the mode pill and a notice, in design pixels.
NOTICE_GAP = 10
CARD_MARGIN = 6
CARD_RADIUS = 18
BUTTON_RADIUS = 10
PAD = 22
STATUS_BASELINE = 34
METER_MIDDLE = 70
BUTTON_TOP = 92
BUTTON_BOTTOM = 130
FOOTER_BASELINE = 152

BAR_COUNT = 58
BAR_GAP = 2
METER_HEIGHT = 20
BAR_MIN_HEIGHT = 1

# The window opens near the bottom-right rather than bottom-centre: chat boxes, search
# bars and command palettes all live at the bottom-centre of a maximised window, which is
# precisely where someone dictating is looking.
EDGE_MARGIN = 24
TASKBAR_ALLOWANCE = 72

RED = "#f06868"
AMBER = "#e9a75f"
ORANGE = "#ffa53a"


@dataclass(frozen=True)
class Look:
    """How each surface is coloured in one state.

    One colour per state was the obvious first shape and it was wrong: painting the dot,
    the timer, all 58 meter bars and the microphone the same red turned the whole card
    into a warning light the moment recording began. The design keeps the waveform teal
    while recording and puts the emphasis on the timer instead.
    """

    words: str
    dot: str
    wave: str
    timer: str
    mic: str


APPEARANCE = {
    "idle": Look("მზადაა", theme.ACCENT, theme.ACCENT, theme.ACCENT, theme.ACCENT),
    "recording": Look("იწერს", RED, theme.ACCENT, "#ffffff", RED),
    "paused": Look("პაუზა", AMBER, AMBER, theme.ACCENT, RED),
    "transcribing": Look("გარდაქმნა", ORANGE, ORANGE, theme.ACCENT, theme.ACCENT),
    "error": Look("შეცდომა", "#f06565", "#f06565", "#f06565", theme.ACCENT),
    "disabled": Look(
        "გამორთულია",
        theme.DISABLED_INK,
        theme.DISABLED_INK,
        theme.DISABLED_INK,
        theme.DISABLED_INK,
    ),
}


@dataclass
class Button:
    """One drawn button: where it is, what it is made of, and what it does."""

    box: tuple[int, int, int, int]
    command: Callable[[], None]
    top: str
    bottom: str
    top_hover: str
    bottom_hover: str
    fill_items: list[int] = field(default_factory=list)
    enabled: bool = True
    hovered: bool = False

    def contains(self, x: int, y: int) -> bool:
        x0, y0, x1, y1 = self.box
        return x0 <= x <= x1 and y0 <= y <= y1


def format_elapsed(seconds: float) -> str:
    whole = int(max(0.0, seconds))
    return f"{whole // 60}:{whole % 60:02d}"


@dataclass(frozen=True)
class Metrics:
    """Design pixels to real ones, at this display's scaling and this user's preference.

    Two factors, not one. `scale` is the display's own scaling times whatever size the
    user asked for; `content_scale` carries a second preference for text and icons.
    Halving the card also halved the writing, which is legible but harder to read at a
    glance than a status display should be — so lettering can grow without the layout
    moving underneath it.
    """

    scale: float
    content_scale: float

    def s(self, value: float) -> int:
        """A design measurement in real screen pixels. Use for anything positional."""
        return round(value * self.scale)

    def c(self, value: float) -> int:
        """The same, for the size of a glyph or the box around a piece of text."""
        return round(value * self.content_scale)

    def font(self, family: str, design_px: int, weight: str = "normal") -> tuple:
        """A font sized in pixels — negative means pixels to Tk, which points would not."""
        return (family, -max(1, self.c(design_px)), weight)

    def card_width(self, *, folded: bool, rewrite: bool) -> int:
        """The card's width in design pixels, for whichever shape it is wearing."""
        if not folded:
            return WINDOW_WIDTH
        return COLLAPSED_WIDTH + (COLLAPSED_MODE_EXTRA if rewrite else 0)

    def card_height(self, *, folded: bool) -> int:
        return COLLAPSED_HEIGHT if folded else WINDOW_HEIGHT

    def inner_edges(self, bleed: int = 0) -> tuple[int, int]:
        """The left and right limits of the open card's content, in screen pixels."""
        left = self.s(CARD_MARGIN + PAD - bleed)
        right = self.s(WINDOW_WIDTH - CARD_MARGIN - PAD + bleed)
        return left, right
