"""The recorder window — the app's whole visible surface.

A tray icon was not enough: Windows 11 hides new tray icons behind the "^" arrow by
default, so the only status indicator the app had was invisible until the user went
looking for it.

The window is frameless, always on top, and draggable. Four things about it are
load-bearing rather than cosmetic:

* **It never takes focus.** `WS_EX_NOACTIVATE` is set on the real window handle, so
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
import ctypes
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


def _point_tcl_at_the_base_installation() -> None:
    """Make tkinter work from inside a virtual environment on Windows.

    A venv copies python.exe but not the Tcl runtime, and the search path tkinter builds
    from `sys.prefix` looks for `lib/tcl8.6` — while the real Python installs it under
    `tcl/tcl8.6`. The result is `TclError: Can't find a usable init.tcl` the moment a
    window is created, which under pythonw.exe means the app dies with no message at all.
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

from voice_typer import widget_theme as theme  # noqa: E402

REFRESH_MS = 70  # fast enough for the level bars to look alive

# Design pixels at 100% scaling. Nothing here is used raw — see `_s`.
WINDOW_WIDTH = 520
WINDOW_HEIGHT = 176
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

STANDARD_DPI = 96.0
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040

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


def display_scale() -> float:
    """How much larger than 100% the display is set to run.

    Read from Windows rather than from Tk: with the process DPI-aware, this is the number
    the desktop is actually using, and it is what keeps the window the same physical size
    while drawing it at full resolution.
    """
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
    except Exception:
        try:
            device = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(device, 88)  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, device)
        except Exception:
            return 1.0
    return max(1.0, dpi / STANDARD_DPI) if dpi else 1.0


def _toplevel_handle(window: tk.Misc) -> int:
    """The window Windows actually manages, not Tk's inner child.

    `winfo_id()` returns a WS_CHILD window. WS_EX_NOACTIVATE and WS_EX_TOOLWINDOW are
    top-level styles and do nothing on a child, so setting them there looked correct and
    achieved nothing: every click on the card still took the foreground, which is why the
    focus had to be handed back afterwards. `wm_frame()` is the real one.
    """
    try:
        return int(window.wm_frame(), 16)
    except Exception:
        return window.winfo_id()


def _make_non_activating(window: tk.Misc) -> None:
    """Stop the window from becoming the foreground window.

    Windows caches a window's frame, so the style change only takes effect once
    SetWindowPos is told the frame changed — and the same call puts the window on top.
    """
    try:
        handle = _toplevel_handle(window)
        user32 = ctypes.windll.user32
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)

        style = get_long(handle, GWL_EXSTYLE)
        set_long(handle, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        user32.SetWindowPos(
            handle,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
        )
    except Exception as exc:
        logger.warning("could not make the window non-activating: %s", exc)


def _format_elapsed(seconds: float) -> str:
    whole = int(max(0.0, seconds))
    return f"{whole // 60}:{whole % 60:02d}"


class OverlayWindow:
    """The floating recorder. `run` blocks and owns the main thread."""

    def __init__(self, controller: Controller, position_path: Path) -> None:
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
        self._scale = display_scale()

        # No withdraw/deiconify here: on Windows a borderless window that is hidden and
        # shown again can come back unmapped, which is exactly as useful as no window.
        self._root = tk.Tk()
        self._build_window()
        self._canvas = tk.Canvas(
            self._root,
            width=self._s(WINDOW_WIDTH),
            height=self._s(WINDOW_HEIGHT),
            highlightthickness=0,
            bg=theme.TRANSPARENT_KEY,
        )
        self._canvas.pack(fill="both", expand=True)
        self._paint_card()
        self._build_menu()
        self._bind_events()
        self._root.update()
        _make_non_activating(self._root)
        self._refresh()

    # ------------------------------------------------------------------------ measuring

    def _s(self, value: float) -> int:
        """A design measurement in real screen pixels."""
        return round(value * self._scale)

    def _font(self, family: str, design_px: int, weight: str = "normal") -> tuple:
        """A font sized in pixels — negative means pixels to Tk, which points would not."""
        return (family, -self._s(design_px), weight)

    # ------------------------------------------------------------------------ the window

    def _build_window(self) -> None:
        self._root.title("voice-typer")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.configure(bg=theme.TRANSPARENT_KEY)
        # Tk sizes point-based fonts from this; keeping it honest stops any widget that
        # does use points from disagreeing with the canvas.
        with contextlib.suppress(tk.TclError):
            self._root.tk.call("tk", "scaling", self._scale * STANDARD_DPI / 72.0)
        # Everything painted in the key colour becomes see-through, which is what gives
        # the card real rounded corners instead of a black box behind them.
        with contextlib.suppress(tk.TclError):
            self._root.attributes("-transparentcolor", theme.TRANSPARENT_KEY)
        x, y = self._restore_position()
        self._saved_position = (x, y)
        self._root.geometry(f"{self._s(WINDOW_WIDTH)}x{self._s(WINDOW_HEIGHT)}+{x}+{y}")
        self._root.protocol("WM_DELETE_WINDOW", lambda: self._safely(self._controller.quit))

    # ------------------------------------------------------------------------- painting

    def _paint_card(self) -> None:
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

    def _inner_edges(self, bleed: int = 0) -> tuple[int, int]:
        left = self._s(CARD_MARGIN + PAD - bleed)
        right = self._s(WINDOW_WIDTH - CARD_MARGIN - PAD + bleed)
        return left, right

    def _paint_status_row(self) -> None:
        left, right = self._inner_edges()
        y = self._s(STATUS_BASELINE)

        self._dot_items = theme.glow_dot(
            self._canvas, left + self._s(5), y, self._s(5), theme.ACCENT, theme.CARD_TOP
        )
        self._status_text = self._canvas.create_text(
            left + self._s(22),
            y,
            text="",
            anchor="w",
            fill=theme.TEXT_BRIGHT,
            font=self._font(theme.UI_FAMILY, theme.STATUS_PX),
        )

        badge = (right - self._s(34), y - self._s(10), right, y + self._s(10))
        theme.rounded_gradient(self._canvas, badge, self._s(5), "#26292c", "#1a1d20")
        theme.rounded_outline(self._canvas, badge, self._s(5), "#3a3e43")
        self._badge_text = self._canvas.create_text(
            (badge[0] + badge[2]) / 2,
            y,
            text="F9",
            fill=theme.TEXT_MUTED,
            font=self._font(theme.MONO_FAMILY, theme.BADGE_PX),
        )

        self._timer_text = self._canvas.create_text(
            right - self._s(46),
            y,
            text="0:00",
            anchor="e",
            fill=theme.ACCENT,
            font=self._font(theme.MONO_FAMILY, theme.MONO_PX),
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
        for name, button in self._buttons.items():
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
        self._record_icon = self._draw_microphone(icon_x, centre_y, theme.ACCENT)
        self._record_label = self._canvas.create_text(
            icon_x + self._s(15),
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

    def _draw_microphone(self, x: float, y: float, colour: str) -> list[int]:
        """A microphone: capsule body, the cradle under it, and the stem.

        Sized against the button rather than copied from the design's own pixels — the
        card here is 58% of the design's width, so the design's 20px glyph would read as
        twice the weight beside a label that did scale down.
        """
        stroke = max(1, self._s(1.4))
        return [
            self._canvas.create_oval(
                x - self._s(2.4),
                y - self._s(6),
                x + self._s(2.4),
                y + self._s(0.5),
                outline=colour,
                width=stroke,
            ),
            self._canvas.create_arc(
                x - self._s(4.6),
                y - self._s(4),
                x + self._s(4.6),
                y + self._s(4.4),
                start=200,
                extent=140,
                style="arc",
                outline=colour,
                width=stroke,
            ),
            self._canvas.create_line(
                x, y + self._s(4.4), x, y + self._s(6.6), fill=colour, width=stroke
            ),
        ]

    def _paint_pause_face(self, box: tuple[int, int, int, int]) -> None:
        centre_y = (box[1] + box[3]) / 2
        icon_x = box[0] + self._s(30)
        self._pause_bars = [
            self._canvas.create_rectangle(
                icon_x + self._s(offset),
                centre_y - self._s(5.5),
                icon_x + self._s(offset + 2.4),
                centre_y + self._s(5.5),
                fill=theme.TEXT_MUTED,
                width=0,
            )
            for offset in (0, 4.8)
        ]
        self._pause_label = self._canvas.create_text(
            icon_x + self._s(15),
            centre_y,
            text="პაუზა",
            anchor="w",
            fill=theme.TEXT_BRIGHT,
            font=self._font(theme.UI_FAMILY, theme.BUTTON_PX),
        )
        self._centre_in(box, [*self._pause_bars, self._pause_label])

    def _paint_cross(self, box: tuple[int, int, int, int], colour: str) -> None:
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        arm = self._s(4)
        stroke = max(1, self._s(1.6))
        self._cancel_ink = [
            self._canvas.create_line(x - arm, y - arm, x + arm, y + arm, fill=colour, width=stroke),
            self._canvas.create_line(x + arm, y - arm, x - arm, y + arm, fill=colour, width=stroke),
        ]

    def _paint_power(self, box: tuple[int, int, int, int], colour: str) -> None:
        """The standard power glyph: a ring open at the top, with a stroke through the gap.

        Tk measures arc angles anticlockwise from three o'clock, so a gap centred on
        twelve o'clock means starting past it and sweeping the rest of the way round.
        """
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2 + self._s(0.5)
        ring = self._s(5.2)
        stroke = max(1, self._s(1.6))
        self._canvas.create_arc(
            x - ring,
            y - ring,
            x + ring,
            y + ring,
            start=125,
            extent=290,
            style="arc",
            outline=colour,
            width=stroke,
        )
        self._canvas.create_line(
            x, y - self._s(7.4), x, y - self._s(1.4), fill=colour, width=stroke
        )

    def _paint_footer(self) -> None:
        left, right = self._inner_edges(bleed=4)
        y = self._s(FOOTER_BASELINE)
        font = self._font(theme.MONO_FAMILY, theme.FOOTER_PX)
        # Kept short on purpose: Consolas has no Georgian, so Tk substitutes a wider font
        # for those runs and a longer line collides with the device name on the right.
        self._canvas.create_text(
            left,
            y,
            text="F9 ჩაწერა · ESC გაუქმება",
            anchor="w",
            fill=theme.TEXT_FAINT,
            font=font,
        )
        self._device_text = self._canvas.create_text(
            right, y, text="", anchor="e", fill=theme.TEXT_FAINT, font=font
        )

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
        self._menu.add_command(label="", state="disabled")  # usage, filled in on open
        self._menu.add_separator()
        self._menu.add_command(
            label="F9-ის მოსმენა", command=lambda: self._safely(self._controller.toggle_enabled)
        )
        self._menu.add_command(
            label="ბოლო ჩანაწერის ხელახლა გაგზავნა",
            command=lambda: self._safely(self._controller.retry_last),
        )
        self._menu.add_separator()
        self._menu.add_command(
            label="ლოგების საქაღალდე", command=lambda: self._safely(self._controller.open_logs)
        )
        self._menu.add_command(
            label="პარამეტრები (config.json)",
            command=lambda: self._safely(self._controller.open_settings),
        )
        self._menu.add_separator()
        self._menu.add_command(
            label="გამორთვა", command=lambda: self._safely(self._controller.quit)
        )

    def _sync_menu(self) -> None:
        """Bring the menu's live entries up to date. Kept apart from showing it because
        `tk_popup` enters Windows' own modal loop and does not return until the menu is
        dismissed — which nothing can do in a test."""
        listening = self._controller.ui_state() != "disabled"
        self._menu.entryconfig(0, label=self._controller.usage_text())
        self._menu.entryconfig(2, label=("✓ F9-ის მოსმენა" if listening else "F9-ის მოსმენა"))

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
        parked on a second monitor was always judged off-screen and dragged back.
        """
        try:
            user32 = ctypes.windll.user32
            left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
            width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            if width and height:
                return left, top, left + width, top + height
        except Exception as exc:
            logger.warning("could not measure the desktop, using the primary screen: %s", exc)
        return 0, 0, self._root.winfo_screenwidth(), self._root.winfo_screenheight()

    def _restore_position(self) -> tuple[int, int]:
        """Put the window back where the user left it, if that is still on screen."""
        width, height = self._s(WINDOW_WIDTH), self._s(WINDOW_HEIGHT)
        margin = self._s(EDGE_MARGIN)
        default = (
            self._root.winfo_screenwidth() - width - margin,
            self._root.winfo_screenheight() - height - self._s(TASKBAR_ALLOWANCE),
        )
        try:
            saved = json.loads(self._position_path.read_text(encoding="utf-8"))
            x, y = int(saved["x"]), int(saved["y"])
        except (OSError, ValueError, KeyError, TypeError):
            return default

        # Enough of the card must remain reachable to grab and drag it back.
        left, top, right, bottom = self._desktop_bounds()
        on_screen_x = left - width + margin < x < right - margin
        on_screen_y = top - margin < y < bottom - margin
        return (x, y) if on_screen_x and on_screen_y else default

    def _save_position(self) -> None:
        """Only when it actually moved — a plain click used to rewrite the file."""
        position = (self._root.winfo_x(), self._root.winfo_y())
        if position == self._saved_position:
            return
        try:
            self._position_path.parent.mkdir(parents=True, exist_ok=True)
            self._position_path.write_text(
                json.dumps({"x": position[0], "y": position[1]}), encoding="utf-8"
            )
            self._saved_position = position
        except OSError as exc:
            logger.warning("could not remember the window position: %s", exc)

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
        self._canvas.itemconfig(self._status_text, text=look.words)
        self._canvas.itemconfig(
            self._timer_text,
            text=_format_elapsed(self._controller.ui_elapsed_seconds()),
            fill=look.timer,
        )
        self._canvas.itemconfig(self._badge_text, text=self._controller.ui_hotkey_label())
        self._canvas.itemconfig(self._device_text, text=self._controller.ui_device_label())

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
