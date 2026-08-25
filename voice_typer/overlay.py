"""The recorder window — the app's whole visible surface.

A tray icon was not enough: Windows 11 hides new tray icons behind the "^" arrow by
default, so the only status indicator the app had was invisible until the user went
looking for it.

The window is frameless, always on top, and draggable. Four things about it are
load-bearing rather than cosmetic:

* **It never takes focus.** `window_platform` marks the real window non-activating, so
  clicking a button here does not move focus away from whatever the user was typing into.
  Even so, Windows can still shift focus on a click, which is why `app.py` remembers the
  window the user was working in and `injector.py` hands focus back before pasting.
* **It only reads.** Every value on screen is polled from the app; the window owns no
  state of its own. That keeps it safe to update from the Tk thread while recording,
  transcription, and timers run on three others.
* **It is drawn, not laid out.** Tk has no gradients, rounded corners, shadows or alpha,
  and the design has all four — so the card is painted on a Canvas. See `widget_theme`.
* **Every measurement below is in design pixels at 100% scaling**, multiplied by the
  display's real scaling factor through `_s`. Drawing at a fixed size and letting Windows
  stretch the result is what makes an overlay look soft and chunky on a scaled display.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


def _point_tcl_at_the_base_installation() -> None:
    """Make tkinter work from inside a virtual environment on Windows.

    A venv copies python.exe but not the Tcl runtime, and the search path tkinter builds
    from `sys.prefix` looks for `lib/tcl8.6` — while the real Python installs it under
    `tcl/tcl8.6`. The result is `TclError: Can't find a usable init.tcl` the moment a
    window is created, which under pythonw.exe means the app dies with no message at all.

    macOS keeps Tcl where tkinter expects it, so the directory this looks for is absent
    and the function does nothing there.
    """
    tcl_root = Path(sys.base_prefix) / "tcl"
    if not tcl_root.is_dir():
        return

    for variable, prefix in (("TCL_LIBRARY", "tcl"), ("TK_LIBRARY", "tk")):
        if os.environ.get(variable):
            continue
        candidates = sorted(tcl_root.glob(f"{prefix}[0-9]*.[0-9]*"), reverse=True)
        if candidates:
            os.environ[variable] = str(candidates[0])


_point_tcl_at_the_base_installation()

import tkinter as tk  # noqa: E402 — must follow the Tcl path fix above

from voice_typer import card_glyphs, window_platform, window_state  # noqa: E402
from voice_typer import widget_theme as theme  # noqa: E402
from voice_typer.window_platform import STANDARD_DPI  # noqa: E402

REFRESH_MS = 70  # fast enough for the level bars to look alive

# Design pixels at 100% scaling. Nothing here is used raw — see `_s`.
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


class Controller(Protocol):
    """What the window needs from the app. Implemented by `App`."""

    def ui_state(self) -> str: ...
    def ui_elapsed_seconds(self) -> float: ...
    def ui_level(self) -> float: ...
    def ui_hotkey_label(self) -> str: ...
    def ui_device_label(self) -> str: ...
    def ui_notice(self) -> str: ...
    def ui_rewrite_mode(self) -> bool: ...
    def copy_raw_text(self) -> None: ...
    def open_rewrite_prompt(self) -> None: ...
    def reload_keys(self) -> None: ...
    def set_rewrite_mode(self, on: bool) -> None: ...
    def toggle_rewrite_mode(self) -> None: ...
    def usage_text(self) -> str: ...
    def toggle_recording(self) -> None: ...
    def toggle_pause(self) -> None: ...
    def cancel_recording(self) -> None: ...
    def toggle_enabled(self) -> None: ...
    def retry_last(self) -> None: ...
    def open_logs(self) -> None: ...
    def open_settings(self) -> None: ...
    def quit(self) -> None: ...


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


def _format_elapsed(seconds: float) -> str:
    whole = int(max(0.0, seconds))
    return f"{whole // 60}:{whole % 60:02d}"


class OverlayWindow:
    """The floating recorder. `run` blocks and owns the main thread."""

    def __init__(
        self,
        controller: Controller,
        position_path: Path,
        window_scale: float = 1.0,
        content_scale: float = 1.0,
    ) -> None:
        self._controller = controller
        self._position_path = position_path
        self._drag_origin: tuple[int, int] | None = None
        self._pressed: str | None = None
        self._closing = False
        self._buttons: dict[str, Button] = {}
        self._bars: list[int] = []
        self._levels = [0.0] * BAR_COUNT
        self._meter_settled = False
        self._meter_colour = ""
        # The display's own scaling, then the user's preference on top of it. Both go
        # through the same multiplier, so a smaller window is drawn small rather than
        # drawn large and shrunk — which is what would make it soft again.
        self._scale = window_platform.display_scale() * window_scale
        # Text and icons carry a second factor. Halving the card also halved the writing,
        # which is legible but harder to read at a glance than it needs to be — and a
        # status display is meant to be read at a glance. Positions still come from
        # `_s`, so the layout does not move when the lettering grows.
        self._content_scale = self._scale * content_scale

        # Folded away or open, and which mode — restored from where the user left them.
        # One record, compared whole in `_save_position`, so a new remembered field can
        # never be added to the file and forgotten in the comparison.
        self._saved_state = window_state.read(self._position_path)
        self._collapsed = self._saved_state.collapsed
        # The window owns the file this was written to, so it restores the value and hands
        # it to the app, which is the one that decides what goes on the clipboard.
        self._rewrite_mode = self._saved_state.rewrite_mode
        controller.set_rewrite_mode(self._rewrite_mode)

        # No withdraw/deiconify here: on Windows a borderless window that is hidden and
        # shown again can come back unmapped, which is exactly as useful as no window.
        self._root = tk.Tk()
        self._build_window()
        self._canvas = tk.Canvas(
            self._root,
            width=self._s(self._card_width()),
            height=self._s(self._card_height()),
            highlightthickness=0,
            bg=self._backdrop,
        )
        self._canvas.pack(fill="both", expand=True)
        self._paint_card()
        self._build_menu()
        self._bind_events()
        self._root.update()
        window_platform.make_non_activating(self._root)
        # After `update`, because there is no NSWindow to configure until Tk has made one.
        window_platform.float_over_full_screen(self._root)
        self._refresh()

    # ------------------------------------------------------------------------ measuring

    def _card_width(self) -> int:
        """The card's width in design pixels, for whichever shape it is wearing."""
        if not self._collapsed:
            return WINDOW_WIDTH
        return COLLAPSED_WIDTH + (COLLAPSED_MODE_EXTRA if self._rewrite_mode else 0)

    def _card_height(self) -> int:
        return COLLAPSED_HEIGHT if self._collapsed else WINDOW_HEIGHT

    def _s(self, value: float) -> int:
        """A design measurement in real screen pixels. Use for anything positional."""
        return round(value * self._scale)

    def _c(self, value: float) -> int:
        """The same, for the size of a glyph or the box around a piece of text.

        Separate from `_s` so lettering and icons can be readable at a card size that
        would otherwise make them squint-small, without the layout shifting around them.
        """
        return round(value * self._content_scale)

    def _font(self, family: str, design_px: int, weight: str = "normal") -> tuple:
        """A font sized in pixels — negative means pixels to Tk, which points would not."""
        return (family, -max(1, self._c(design_px)), weight)

    # ------------------------------------------------------------------------ the window

    def _build_window(self) -> None:
        self._root.title("voice-typer")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        # Tk sizes point-based fonts from this; keeping it honest stops any widget that
        # does use points from disagreeing with the canvas.
        with contextlib.suppress(tk.TclError):
            self._root.tk.call("tk", "scaling", self._scale * STANDARD_DPI / 72.0)
        # Whatever is painted outside the card has to disappear, which is what gives the
        # card real rounded corners instead of a black box behind them. Each system does
        # that differently, and one of them may refuse — so the colour to paint with is
        # whatever came back, not a constant.
        self._backdrop = window_platform.apply_transparency(
            self._root, theme.TRANSPARENT_KEY, theme.CARD_TOP
        )
        self._root.configure(bg=self._backdrop)
        x, y = self._restore_position()
        # Where the card actually opened, which is not always what the file asked for —
        # an unreachable corner falls back. Recording the real one is what stops the
        # first plain click from rewriting the file with the same values.
        self._saved_state = replace(self._saved_state, position=(x, y))
        self._root.geometry(f"{self._s(self._card_width())}x{self._s(self._card_height())}+{x}+{y}")
        self._root.protocol("WM_DELETE_WINDOW", lambda: self._safely(self._controller.quit))

    # ------------------------------------------------------------------------- painting

    def _paint_card(self) -> None:
        if self._collapsed:
            self._paint_collapsed_card()
            return

        margin = self._s(CARD_MARGIN)
        card = (
            margin,
            margin,
            self._s(WINDOW_WIDTH) - margin,
            self._s(WINDOW_HEIGHT) - margin,
        )
        theme.rounded_gradient(
            self._canvas, card, self._s(CARD_RADIUS), theme.CARD_TOP, theme.CARD_BOTTOM
        )
        theme.rounded_outline(self._canvas, card, self._s(CARD_RADIUS), theme.CARD_BORDER)

        self._paint_status_row()
        self._paint_meter()
        self._paint_buttons()
        self._paint_footer()

    def _paint_collapsed_card(self) -> None:
        """The folded strip: the state light, the running time, and the way back.

        Deliberately not a smaller copy of the card. Everything that was left out is
        something the user cannot act on without looking — and someone who folded the
        window away is not looking at it.
        """
        margin = self._s(CARD_MARGIN)
        card = (
            margin,
            margin,
            self._s(self._card_width()) - margin,
            self._s(COLLAPSED_HEIGHT) - margin,
        )
        radius = self._s(COLLAPSED_RADIUS)
        theme.rounded_gradient(self._canvas, card, radius, theme.CARD_TOP, theme.CARD_BOTTOM)
        theme.rounded_outline(self._canvas, card, radius, theme.CARD_BORDER)

        y = round((card[1] + card[3]) / 2)  # a gradient is painted row by row: whole pixels
        left = card[0] + self._s(COLLAPSED_PAD)
        right = card[2] - self._s(COLLAPSED_PAD)

        self._dot_items = theme.glow_dot(
            self._canvas, left + self._c(5), y, self._c(5), theme.ACCENT, theme.CARD_TOP
        )
        toggle = (right - self._c(20), y - self._c(10), right, y + self._c(10))
        self._paint_fold_button(toggle, pointing_up=True)

        edge = toggle[0] - self._s(10)
        if self._rewrite_mode:
            # Only in the mode that changes what gets pasted. The ordinary mode says
            # nothing, so anything the folded strip does say is worth reading.
            pill = (edge - self._s(MODE_PILL_WIDTH * 0.72), y - self._c(9), edge, y + self._c(9))
            self._paint_mode_button(pill)
            edge = pill[0] - self._s(8)

        self._timer_text = self._canvas.create_text(
            edge,
            y,
            text="0:00",
            anchor="e",
            fill=theme.ACCENT,
            font=self._font(theme.MONO_FAMILY, theme.MONO_PX),
        )

    def _inner_edges(self, bleed: int = 0) -> tuple[int, int]:
        left = self._s(CARD_MARGIN + PAD - bleed)
        right = self._s(WINDOW_WIDTH - CARD_MARGIN - PAD + bleed)
        return left, right

    def _paint_status_row(self) -> None:
        left, right = self._inner_edges()
        y = self._s(STATUS_BASELINE)

        self._dot_items = theme.glow_dot(
            self._canvas, left + self._c(5), y, self._c(5), theme.ACCENT, theme.CARD_TOP
        )
        self._status_text = self._canvas.create_text(
            left + self._c(22),
            y,
            text="",
            anchor="w",
            fill=theme.TEXT_BRIGHT,
            font=self._font(theme.UI_FAMILY, theme.STATUS_PX),
        )

        # The fold control sits in the corner rather than in the button row: that row is
        # for what to do with a recording, and folding the window is not one of those.
        fold = (right - self._c(20), y - self._c(10), right, y + self._c(10))
        self._paint_fold_button(fold, pointing_up=False)

        # The badge is sized with the lettering inside it rather than with the card, or
        # a larger "F9" would push against its own border.
        badge_right = fold[0] - self._s(10)
        badge = (badge_right - self._c(34), y - self._c(10), badge_right, y + self._c(10))
        theme.rounded_gradient(self._canvas, badge, self._c(5), "#26292c", "#1a1d20")
        theme.rounded_outline(self._canvas, badge, self._c(5), "#3a3e43")
        self._badge_text = self._canvas.create_text(
            (badge[0] + badge[2]) / 2,
            y,
            text="F9",
            fill=theme.TEXT_MUTED,
            font=self._font(theme.MONO_FAMILY, theme.BADGE_PX),
        )

        # Measured from the badge, not from the card's edge: the badge is what the timer
        # would collide with, and it is the thing whose width changes.
        self._timer_text = self._canvas.create_text(
            badge[0] - self._s(12),
            y,
            text="0:00",
            anchor="e",
            fill=theme.ACCENT,
            font=self._font(theme.MONO_FAMILY, theme.MONO_PX),
        )

    def _paint_mode_button(self, box: tuple[int, int, int, int]) -> None:
        """Which of the two things F9 does, shown as a word and switched by clicking it.

        Both shapes of the card paint this the same way, so there is exactly one place
        where the mode is drawn and exactly one where it is read.
        """
        box = tuple(round(edge) for edge in box)  # type: ignore[assignment]
        rewrite = self._rewrite_mode
        button = Button(
            box,
            self.toggle_rewrite_mode,
            theme.BUTTON_TOP,
            theme.BUTTON_BOTTOM,
            theme.BUTTON_TOP_HOVER,
            theme.BUTTON_BOTTOM_HOVER,
        )
        radius = self._s(7)
        button.fill_items = theme.rounded_gradient(
            self._canvas, box, radius, button.top, button.bottom
        )
        theme.rounded_outline(self._canvas, box, radius, ORANGE if rewrite else theme.BUTTON_BORDER)
        self._buttons["mode"] = button

        self._canvas.create_text(
            (box[0] + box[2]) / 2,
            (box[1] + box[3]) / 2,
            text="გამართვა" if rewrite else "სიტყვები",
            fill=ORANGE if rewrite else theme.TEXT_MUTED,
            font=self._font(theme.UI_FAMILY, theme.FOOTER_PX + 1),
        )

    def _paint_fold_button(self, box: tuple[int, int, int, int], *, pointing_up: bool) -> None:
        """The one control both shapes of the card have: fold away, or open back up."""
        box = tuple(round(edge) for edge in box)  # type: ignore[assignment]
        button = Button(
            box,
            self.toggle_collapsed,
            theme.BUTTON_TOP,
            theme.BUTTON_BOTTOM,
            theme.BUTTON_TOP_HOVER,
            theme.BUTTON_BOTTOM_HOVER,
        )
        radius = self._s(6)
        button.fill_items = theme.rounded_gradient(
            self._canvas, box, radius, button.top, button.bottom
        )
        theme.rounded_outline(self._canvas, box, radius, theme.BUTTON_BORDER)
        self._buttons["fold"] = button

        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        arm = self._c(3.8)
        rise = self._c(2.2)
        stroke = max(1, self._c(1.5))
        tip_y = y - rise if pointing_up else y + rise
        base_y = y + rise if pointing_up else y - rise
        for side in (-arm, arm):
            self._canvas.create_line(
                x + side, base_y, x, tip_y, fill=theme.TEXT_MUTED, width=stroke
            )

    def _paint_meter(self) -> None:
        left, right = self._inner_edges(bleed=4)
        middle = self._s(METER_MIDDLE)

        self._canvas.create_line(
            left, middle, right, middle, fill=theme.blend(theme.ACCENT, theme.CARD_TOP, 0.35)
        )

        gap = self._s(BAR_GAP)
        span = (right - left - gap * (BAR_COUNT - 1)) / BAR_COUNT
        for index in range(BAR_COUNT):
            x = left + index * (span + gap)
            self._bars.append(
                self._canvas.create_rectangle(
                    x,
                    middle - 0.5,
                    x + span,
                    middle + 0.5,
                    fill=theme.blend(theme.ACCENT, theme.CARD_TOP, 0.5),
                    width=0,
                )
            )

    def _paint_buttons(self) -> None:
        left, right = self._inner_edges(bleed=4)
        top, bottom = self._s(BUTTON_TOP), self._s(BUTTON_BOTTOM)
        square = bottom - top
        gap = self._s(8)

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
        self._buttons["record"] = Button(record_box, self._controller.toggle_recording, *plain)
        self._buttons["pause"] = Button(pause_box, self._controller.toggle_pause, *plain)
        self._buttons["cancel"] = Button(
            cancel_box,
            self._controller.cancel_recording,
            theme.CANCEL_TOP,
            theme.CANCEL_BOTTOM,
            "#3d3327",
            "#1d1916",
        )
        self._buttons["power"] = Button(
            power_box,
            self._controller.quit,
            theme.POWER_TOP,
            theme.POWER_BOTTOM,
            "#3d2a2c",
            "#1d1718",
        )

        radius = self._s(BUTTON_RADIUS)
        borders = {"cancel": theme.CANCEL_BORDER, "power": theme.POWER_BORDER}
        # Only the four painted here. The fold control is already drawn, with its own
        # size and its own chevron, and painting it a second time would bury the chevron.
        for name in ("record", "pause", "cancel", "power"):
            button = self._buttons[name]
            button.fill_items = theme.rounded_gradient(
                self._canvas, button.box, radius, button.top, button.bottom
            )
            theme.rounded_outline(
                self._canvas, button.box, radius, borders.get(name, theme.BUTTON_BORDER)
            )

        self._paint_record_face(record_box)
        self._paint_pause_face(pause_box)
        self._paint_cross(cancel_box, theme.CANCEL_INK)
        self._paint_power(power_box, theme.POWER_INK)

    def _paint_record_face(self, box: tuple[int, int, int, int]) -> None:
        centre_y = (box[1] + box[3]) / 2
        icon_x = box[0] + self._s(30)
        self._record_icon = card_glyphs.draw_microphone(
            self._canvas, icon_x, centre_y, theme.ACCENT, self._c
        )
        self._record_label = self._canvas.create_text(
            icon_x + self._c(15),
            centre_y,
            text="ჩაწერა",
            anchor="w",
            fill=theme.TEXT_BRIGHT,
            font=self._font(theme.UI_FAMILY, theme.BUTTON_PX),
        )
        self._centre_in(box, [*self._record_icon, self._record_label])

    def _centre_in(self, box: tuple[int, int, int, int], items: list[int]) -> None:
        """Slide a button's icon and label so the pair sits centred in it.

        Measured rather than computed: the label's width depends on the font Windows
        picked for Georgian, and it changes when the label does. Doing this after every
        text change is also what stops the group jumping sideways between states.
        """
        bounds = self._canvas.bbox(*items)
        if bounds is None:
            return
        wanted = (box[0] + box[2]) / 2
        current = (bounds[0] + bounds[2]) / 2
        shift = round(wanted - current)
        if shift:
            for item in items:
                self._canvas.move(item, shift, 0)

    def _paint_pause_face(self, box: tuple[int, int, int, int]) -> None:
        centre_y = (box[1] + box[3]) / 2
        icon_x = box[0] + self._s(30)
        self._pause_bars = card_glyphs.draw_pause_bars(
            self._canvas, icon_x, centre_y, theme.TEXT_MUTED, self._c
        )
        self._pause_label = self._canvas.create_text(
            icon_x + self._c(15),
            centre_y,
            text="პაუზა",
            anchor="w",
            fill=theme.TEXT_BRIGHT,
            font=self._font(theme.UI_FAMILY, theme.BUTTON_PX),
        )
        self._centre_in(box, [*self._pause_bars, self._pause_label])

    def _paint_cross(self, box: tuple[int, int, int, int], colour: str) -> None:
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        self._cancel_ink = card_glyphs.draw_cross(self._canvas, x, y, colour, self._c)

    def _paint_power(self, box: tuple[int, int, int, int], colour: str) -> None:
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2 + self._s(0.5)
        card_glyphs.draw_power(self._canvas, x, y, colour, self._c)

    def _fit_text(self, item: int, text: str, room: int) -> None:
        """Put text on an item, trimmed with an ellipsis until it fits.

        Measured rather than counted, for the reason `_centre_in` already gives: how
        wide a Georgian string comes out depends on the font Windows picked for it, so
        a character budget is a guess and `bbox` is an answer.
        """
        self._canvas.itemconfig(item, text=text)
        if not text:
            return
        while len(text) > 1:
            bounds = self._canvas.bbox(item)
            if bounds is None or bounds[2] - bounds[0] <= room:
                return
            text = text[:-2] + "…"
            self._canvas.itemconfig(item, text=text)

    def _paint_footer(self) -> None:
        left, right = self._inner_edges(bleed=4)
        y = self._s(FOOTER_BASELINE)
        font = self._font(theme.MONO_FAMILY, theme.FOOTER_PX)
        # The mode lives here rather than in the button row: that row is for what to do
        # with a recording, and this decides what happens to the words afterwards. It also
        # has to be readable at a glance, which the footer line is and a fifth square
        # button next to four others would not be.
        pill = (
            left,
            y - self._s(MODE_PILL_HEIGHT / 2),
            left + self._s(MODE_PILL_WIDTH),
            y + self._s(MODE_PILL_HEIGHT / 2),
        )
        self._paint_mode_button(pill)
        # Kept short on purpose: Consolas has no Georgian, so Tk substitutes a wider font
        # for those runs and a longer line collides with the mode pill on the left.
        self._shown_notice = ""  # so a re-fit only happens when the message changes
        self._device_text = self._canvas.create_text(
            right, y, text="", anchor="e", fill=theme.TEXT_FAINT, font=font
        )
        # What the app had to say, in the space the microphone name usually occupies. It
        # goes here rather than on the status row because an overlapping take can bring a
        # message in while the next recording is already running, and hiding "იწერს" to
        # show it would make the card lie about what it is doing. In its own family, not
        # the footer's monospace: that font has no Georgian at all.
        self._notice_text = self._canvas.create_text(
            left + self._s(MODE_PILL_WIDTH) + self._s(NOTICE_GAP),
            y,
            text="",
            anchor="w",
            fill=AMBER,
            font=self._font(theme.UI_FAMILY, theme.FOOTER_PX),
        )
        self._notice_room = right - self._s(MODE_PILL_WIDTH) - self._s(NOTICE_GAP) - left

    # ---------------------------------------------------------------------- folding away

    def toggle_collapsed(self) -> None:
        """Fold the card down to a strip, or open it back up.

        Nothing about recording changes: the hotkey listener never knew this window
        existed, so F9 works folded, unfolded, and behind a full-screen application alike.
        """
        self._collapsed = not self._collapsed
        self._rebuild()
        self._save_position()

    def toggle_rewrite_mode(self) -> None:
        """Switch between pasting the words and pasting them with the instruction.

        The app owns the setting; the window owns showing it and the file it is
        remembered in. Repainting is not optional — the folded strip is a different width
        in the two modes, because the mode has to stay visible with the card folded away.
        """
        self._controller.toggle_rewrite_mode()
        self._rewrite_mode = self._controller.ui_rewrite_mode()
        self._rebuild()
        self._save_position()

    def _rebuild(self) -> None:
        """Throw the drawing away and paint the other shape at the same corner.

        Everything the canvas handed out — item ids, buttons, the meter's bars — belongs
        to the drawing that is being deleted, so every one of them is reset here. A stale
        id is not an error in Tk; it is a silent no-op, which is how a window ends up
        looking frozen.
        """
        self._canvas.delete("all")
        self._buttons = {}
        self._bars = []
        self._levels = [0.0] * BAR_COUNT
        self._meter_settled = False
        self._meter_colour = ""
        self._pressed = None
        self._drag_origin = None

        width = self._s(self._card_width())
        height = self._s(self._card_height())
        # Unfolding at the bottom-right corner would otherwise push most of the card off
        # the screen — which is exactly where this window is by default.
        x, y = window_state.clamp_to_desktop(
            (self._root.winfo_x(), self._root.winfo_y()),
            width=width,
            height=height,
            bounds=self._desktop_bounds(),
        )

        self._canvas.config(width=width, height=height)
        self._root.geometry(f"{width}x{height}+{x}+{y}")
        self._paint_card()
        self._update()

    # -------------------------------------------------------------------------- events

    def _bind_events(self) -> None:
        self._canvas.bind("<Button-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Motion>", self._on_move)
        self._canvas.bind("<Leave>", lambda _e: self._set_hover(None))
        self._canvas.bind("<Button-3>", self._show_menu)

    def _button_at(self, x: int, y: int) -> str | None:
        for name, button in self._buttons.items():
            if button.enabled and button.contains(x, y):
                return name
        return None

    def _on_press(self, event: tk.Event) -> None:
        self._pressed = self._button_at(event.x, event.y)
        if self._pressed is None:
            self._drag_origin = (
                event.x_root - self._root.winfo_x(),
                event.y_root - self._root.winfo_y(),
            )

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        offset_x, offset_y = self._drag_origin
        self._root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _on_release(self, event: tk.Event) -> None:
        if self._drag_origin is not None:
            self._drag_origin = None
            self._pressed = None
            self._save_position()
            return

        name = self._button_at(event.x, event.y)
        pressed, self._pressed = self._pressed, None
        if name is not None and name == pressed:
            self._safely(self._buttons[name].command)

    def _on_move(self, event: tk.Event) -> None:
        self._set_hover(self._button_at(event.x, event.y))

    def _set_hover(self, name: str | None) -> None:
        for key, button in self._buttons.items():
            wanted = key == name
            if wanted == button.hovered:
                continue
            button.hovered = wanted
            top = button.top_hover if wanted else button.top
            bottom = button.bottom_hover if wanted else button.bottom
            theme.recolour_gradient(self._canvas, button.fill_items, top, bottom)
        self._canvas.config(cursor="hand2" if name else "")

    def _safely(self, command: Callable[[], None]) -> None:
        """A button must never be able to take the window down with it."""
        try:
            command()
        except Exception:
            logger.exception("a window button raised")

    # ---------------------------------------------------------------------------- menu

    def _build_menu(self) -> None:
        """Right-click menu — everything the hidden tray icon used to offer."""
        self._menu = tk.Menu(
            self._root,
            tearoff=0,
            bg="#26292c",
            fg=theme.TEXT_BRIGHT,
            activebackground="#3a3e43",
            activeforeground=theme.TEXT_BRIGHT,
            borderwidth=0,
        )
        # Every entry whose label changes is remembered by name as it is added. Counting
        # positions by hand is how a menu ends up relabelling the wrong line the next time
        # somebody inserts an item above it.
        self._menu_index: dict[str, int] = {}
        self._add_menu_entry("usage", label="", state="disabled")  # filled in on open
        self._menu.add_separator()
        self._add_menu_entry(
            "listening",
            label="F9-ის მოსმენა",
            command=lambda: self._safely(self._controller.toggle_enabled),
        )
        self._menu.add_command(
            label="ბოლო ჩანაწერის ხელახლა გაგზავნა",
            command=lambda: self._safely(self._controller.retry_last),
        )
        self._menu.add_command(
            label="ნედლი ტექსტი clipboard-ში",
            command=lambda: self._safely(self._controller.copy_raw_text),
        )
        self._add_menu_entry(
            "mode",
            label="გამართვის რეჟიმი",
            command=lambda: self._safely(self.toggle_rewrite_mode),
        )
        self._add_menu_entry(
            "fold", label="ჩაკეცვა", command=lambda: self._safely(self.toggle_collapsed)
        )
        self._menu.add_separator()
        self._menu.add_command(
            label="ლოგების საქაღალდე", command=lambda: self._safely(self._controller.open_logs)
        )
        self._menu.add_command(
            label="პარამეტრები (config.json)",
            command=lambda: self._safely(self._controller.open_settings),
        )
        self._menu.add_command(
            label="გამართვის ინსტრუქცია (rewrite-prompt.md)",
            command=lambda: self._safely(self._controller.open_rewrite_prompt),
        )
        self._menu.add_command(
            label="API გასაღებები…", command=lambda: self._safely(self._open_keys)
        )
        self._menu.add_separator()
        self._menu.add_command(
            label="გამორთვა", command=lambda: self._safely(self._controller.quit)
        )

    def _open_keys(self) -> None:
        """The keys window, as a child of this one.

        Opened here rather than in `app.py` because Tk allows exactly one root and this
        object owns it — a second `tk.Tk()` from the state machine would be a second
        event loop and a hung window. The app is told afterwards, so a key typed in just
        now takes effect on the next dictation rather than after a restart.

        Imported inside the function: the window is a rare path and the module pulls in
        the whole first-run screen, which nothing else here needs.
        """
        from voice_typer.first_run import ask_for_keys

        if ask_for_keys(self._root):
            self._controller.reload_keys()

    def _add_menu_entry(self, name: str, **options) -> None:
        """Add an entry and remember where it landed, for `_sync_menu` to find later."""
        self._menu.add_command(**options)
        self._menu_index[name] = self._menu.index("end")

    def _sync_menu(self) -> None:
        """Bring the menu's live entries up to date. Kept apart from showing it because
        `tk_popup` enters Windows' own modal loop and does not return until the menu is
        dismissed — which nothing can do in a test."""
        listening = self._controller.ui_state() != "disabled"
        self._menu.entryconfig(self._menu_index["usage"], label=self._controller.usage_text())
        self._menu.entryconfig(
            self._menu_index["listening"],
            label=("✓ F9-ის მოსმენა" if listening else "F9-ის მოსმენა"),
        )
        self._menu.entryconfig(
            self._menu_index["mode"],
            label=("✓ გამართვის რეჟიმი" if self._rewrite_mode else "გამართვის რეჟიმი"),
        )
        self._menu.entryconfig(
            self._menu_index["fold"], label=("გაშლა" if self._collapsed else "ჩაკეცვა")
        )

    def _show_menu(self, event: tk.Event) -> None:
        self._sync_menu()
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    # ------------------------------------------------------------------------ position

    def _desktop_bounds(self) -> tuple[int, int, int, int]:
        """The whole desktop, not just the primary screen.

        `winfo_screenwidth` reports only the primary display, so a window the user had
        parked on a second monitor was always judged off-screen and dragged back. Where
        the system will not give the full extent, the primary screen is the honest answer.
        """
        bounds = window_platform.desktop_bounds()
        if bounds is not None:
            return bounds
        return 0, 0, self._root.winfo_screenwidth(), self._root.winfo_screenheight()

    def _restore_position(self) -> tuple[int, int]:
        """Put the window back where the user left it, if that is still on screen.

        Only the Tk questions live here — how big this card is and how big the screen is.
        Whether the remembered corner is still reachable is `window_state`'s decision,
        which is why it can be tested without a desktop.
        """
        width, height = self._s(self._card_width()), self._s(self._card_height())
        margin = self._s(EDGE_MARGIN)
        return window_state.choose_position(
            window_state.read(self._position_path).position,
            default=(
                self._root.winfo_screenwidth() - width - margin,
                self._root.winfo_screenheight() - height - self._s(TASKBAR_ALLOWANCE),
            ),
            width=width,
            margin=margin,
            bounds=self._desktop_bounds(),
        )

    def _save_position(self) -> None:
        """Only when something actually changed — a plain click used to rewrite the file."""
        current = window_state.SavedWindow(
            position=(self._root.winfo_x(), self._root.winfo_y()),
            collapsed=self._collapsed,
            rewrite_mode=self._rewrite_mode,
        )
        if current == self._saved_state:
            return
        # Remembered only once it is really on disk; a failed write must not leave the
        # next comparison believing the file already agrees.
        if window_state.write(self._position_path, current):
            self._saved_state = current

    # -------------------------------------------------------------------------- refresh

    def _refresh(self) -> None:
        """The one place Tk is touched on a schedule. Also how a close from another
        thread reaches the Tk thread — `close` only sets a flag, because calling into Tk
        from outside its own thread is how Tk applications die mysteriously."""
        if self._closing:
            self._destroy()
            return
        try:
            self._update()
        except Exception:
            logger.exception("painting the window failed")
        self._root.after(REFRESH_MS, self._refresh)

    def _update(self) -> None:
        state = self._controller.ui_state()
        look = APPEARANCE.get(state, APPEARANCE["idle"])

        theme.recolour_glow_dot(self._canvas, self._dot_items, look.dot, theme.CARD_TOP)
        self._canvas.itemconfig(
            self._timer_text,
            text=_format_elapsed(self._controller.ui_elapsed_seconds()),
            fill=look.timer,
        )
        if self._collapsed:
            # Everything below belongs to items the folded card never painted. Reaching
            # for one of them would raise on every tick, fourteen times a second.
            return

        self._canvas.itemconfig(self._status_text, text=look.words)
        self._canvas.itemconfig(self._badge_text, text=self._controller.ui_hotkey_label())
        # One line, two things to say. The message wins while it lasts: which microphone
        # is in use is the least urgent thing on the card, and the only reason someone
        # reads that line at all is to find out why something did not happen.
        notice = self._controller.ui_notice()
        self._canvas.itemconfig(
            self._device_text, text="" if notice else self._controller.ui_device_label()
        )
        if notice != self._shown_notice:
            self._fit_text(self._notice_text, notice, self._notice_room)
            self._shown_notice = notice

        self._update_meter(state, look.wave)
        self._update_buttons(state, look)

    def _update_meter(self, state: str, colour: str) -> None:
        """Scroll the level history leftwards, newest at the right — a recorder's trace."""
        level = self._controller.ui_level() if state == "recording" else 0.0
        level = 0.0 if level != level else min(1.0, max(0.0, level))  # NaN reads as 0
        self._levels = [*self._levels[1:], level]

        # Fourteen times a second, redrawing 58 rectangles that are all already flat is
        # most of what this window costs while it sits there doing nothing.
        settled = not any(self._levels)
        if settled and self._meter_settled and colour == self._meter_colour:
            return
        self._meter_settled = settled
        self._meter_colour = colour

        middle = self._s(METER_MIDDLE)
        tallest = self._s(METER_HEIGHT) - self._s(2)
        floor = self._s(BAR_MIN_HEIGHT)
        faded = theme.blend(colour, theme.CARD_TOP, 0.45)

        for index, bar in enumerate(self._bars):
            height = max(floor, self._levels[index] * tallest)
            x0, _, x1, _ = self._canvas.coords(bar)
            self._canvas.coords(bar, x0, middle - height / 2, x1, middle + height / 2)
            self._canvas.itemconfig(bar, fill=colour if height > floor else faded)

    def _update_buttons(self, state: str, look: Look) -> None:
        busy = state in ("recording", "paused")

        self._set_enabled("record", state not in ("transcribing", "disabled"))
        self._set_enabled("pause", busy)
        self._set_enabled("cancel", busy)

        self._set_label(self._record_label, "გაჩერება" if busy else "ჩაწერა", "record")
        self._set_label(self._pause_label, "გაგრძელება" if state == "paused" else "პაუზა", "pause")

        # A bright cyan microphone beside a greyed-out label reads as a live button.
        ink = look.mic if self._buttons["record"].enabled else theme.DISABLED_INK
        for index, item in enumerate(self._record_icon):
            # The oval and the arc take `outline`; the stem is a line and takes `fill`.
            option = "outline" if index < 2 else "fill"
            self._canvas.itemconfig(item, **{option: ink})

    def _set_label(self, item: int, text: str, button: str) -> None:
        """Change a button's caption and re-centre its contents around the new width."""
        if self._canvas.itemcget(item, "text") == text:
            return
        self._canvas.itemconfig(item, text=text)
        group = self._record_icon if button == "record" else self._pause_bars
        self._centre_in(self._buttons[button].box, [*group, item])

    def _set_enabled(self, name: str, enabled: bool) -> None:
        button = self._buttons[name]
        if button.enabled == enabled:
            return
        button.enabled = enabled

        if not enabled and button.hovered:
            # Otherwise a button that switches off under the pointer keeps its lit
            # gradient and its hand cursor, and the next click there drags the window.
            button.hovered = False
            theme.recolour_gradient(self._canvas, button.fill_items, button.top, button.bottom)
            self._canvas.config(cursor="")

        if name == "record":
            self._canvas.itemconfig(
                self._record_label, fill=theme.TEXT_BRIGHT if enabled else theme.DISABLED_INK
            )
        elif name == "pause":
            ink = theme.TEXT_BRIGHT if enabled else theme.DISABLED_INK
            self._canvas.itemconfig(self._pause_label, fill=ink)
            for bar in self._pause_bars:
                self._canvas.itemconfig(
                    bar, fill=theme.TEXT_MUTED if enabled else theme.DISABLED_INK
                )
        elif name == "cancel":
            for item in self._cancel_ink:
                self._canvas.itemconfig(
                    item, fill=theme.CANCEL_INK if enabled else theme.DISABLED_INK
                )

    # ------------------------------------------------------------------------ lifecycle

    def run(self) -> None:
        """Show the window and block until it is closed."""
        self._root.mainloop()

    def close(self) -> None:
        """Ask the window to shut. Safe from any thread — it only sets a flag, which the
        refresh loop picks up on the Tk thread within one tick."""
        self._closing = True

    def _destroy(self) -> None:
        self._save_position()
        with contextlib.suppress(Exception):
            self._root.destroy()
