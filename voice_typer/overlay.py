"""The little window that shows what the app is doing.

A tray icon was not enough: Windows 11 hides new tray icons behind the "^" arrow by
default, so the only status indicator the app had was invisible until the user went
looking for it.

The window is frameless, always on top, and draggable. Two things about it are load-
bearing rather than cosmetic:

* **It never takes focus.** `WS_EX_NOACTIVATE` is set on the real window handle, so
  clicking a button here does not move focus away from whatever the user was typing into.
  Without that, pressing Stop would make this window the foreground one and the transcript
  would be pasted into nothing.
* **It only reads.** Every value on screen is polled from the app; the window owns no
  state of its own. That keeps it safe to update from the Tk thread while recording,
  transcription, and timers run on three others.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


def _point_tcl_at_the_base_installation() -> None:
    """Make tkinter work from inside a virtual environment on Windows.

    A venv copies python.exe but not the Tcl runtime, and the search path tkinter builds
    from `sys.prefix` looks for `lib/tcl8.6` — while the real Python installs it under
    `tcl/tcl8.6`. The result is `TclError: Can't find a usable init.tcl` the moment a
    window is created, which under pythonw.exe means the app dies with no message at all.

    Setting these two variables before Tk starts is the documented way out. Existing
    values are left alone.
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

REFRESH_MS = 80  # fast enough for the level bar to look alive
WINDOW_WIDTH = 320
WINDOW_HEIGHT = 100

# The window opens near the bottom-right rather than bottom-centre: chat boxes, search
# bars and command palettes all live at the bottom-centre of a maximised window, which is
# precisely where someone dictating is looking.
EDGE_MARGIN = 24
TASKBAR_ALLOWANCE = 64

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040

BACKGROUND = "#1c1c1e"
BORDER = "#3a3a3c"
TEXT = "#f2f2f7"
DIM_TEXT = "#8e8e93"
BUTTON_BACKGROUND = "#2c2c2e"
BUTTON_ACTIVE = "#3a3a3c"
METER_EMPTY = "#2c2c2e"

# Colour and words for each thing the app can be doing.
APPEARANCE = {
    "idle": ("#8a8a8a", "მზადაა"),
    "recording": ("#ff453a", "იწერს"),
    "paused": ("#ffd60a", "დაპაუზებულია"),
    "transcribing": ("#ff9f0a", "გარდაქმნა..."),
    "error": ("#7a1512", "შეცდომა"),
    "disabled": ("#48484a", "გამორთულია"),
}


class Controller(Protocol):
    """What the window needs from the app. Implemented by `App`."""

    def ui_state(self) -> str: ...
    def ui_elapsed_seconds(self) -> float: ...
    def ui_level(self) -> float: ...
    def ui_hotkey_label(self) -> str: ...
    def toggle_recording(self) -> None: ...
    def toggle_pause(self) -> None: ...
    def cancel_recording(self) -> None: ...
    def toggle_enabled(self) -> None: ...
    def quit(self) -> None: ...


def _make_non_activating(window: tk.Misc) -> None:
    """Stop the window from ever becoming the foreground window, and show it.

    Not taking focus is the difference between the transcript landing in the user's
    document and landing nowhere. Windows caches a window's frame, so the style change
    only takes effect once SetWindowPos is told the frame changed — and the same call is
    what puts the window on top and makes it visible.
    """
    try:
        handle = window.winfo_id()
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
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


class OverlayWindow:
    """The floating status window. `run` blocks and owns the main thread."""

    def __init__(self, controller: Controller, position_path: Path) -> None:
        self._controller = controller
        self._position_path = position_path
        self._drag_origin: tuple[int, int] | None = None
        self._closing = False

        # No withdraw/deiconify here: on Windows a borderless window that is hidden and
        # shown again can come back unmapped, which is exactly as useful as no window.
        self._root = tk.Tk()
        self._build_window()
        self._build_widgets()
        self._root.update()
        _make_non_activating(self._root)
        self._refresh()

    # ------------------------------------------------------------------------ building

    def _build_window(self) -> None:
        self._root.title("voice-typer")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.configure(bg=BORDER)
        x, y = self._restore_position()
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")
        self._root.protocol("WM_DELETE_WINDOW", self._on_quit)

    def _build_widgets(self) -> None:
        outer = tk.Frame(self._root, bg=BACKGROUND)
        outer.pack(fill="both", expand=True, padx=1, pady=1)

        top = tk.Frame(outer, bg=BACKGROUND)
        top.pack(fill="x", padx=10, pady=(8, 2))

        self._dot = tk.Canvas(top, width=12, height=12, bg=BACKGROUND, highlightthickness=0)
        self._dot_id = self._dot.create_oval(1, 1, 11, 11, fill=APPEARANCE["idle"][0], width=0)
        self._dot.pack(side="left")

        self._status = tk.Label(
            top, text="", bg=BACKGROUND, fg=TEXT, font=("Segoe UI", 10), anchor="w"
        )
        self._status.pack(side="left", padx=(8, 0))

        self._timer = tk.Label(
            top, text="0:00", bg=BACKGROUND, fg=DIM_TEXT, font=("Consolas", 10), anchor="e"
        )
        self._timer.pack(side="right")

        self._hotkey_hint = tk.Label(
            top, text="", bg=BACKGROUND, fg=DIM_TEXT, font=("Segoe UI", 9), anchor="e"
        )
        self._hotkey_hint.pack(side="right", padx=(0, 10))

        self._meter = tk.Canvas(outer, height=6, bg=METER_EMPTY, highlightthickness=0)
        self._meter.pack(fill="x", padx=10, pady=(4, 6))
        self._meter_bar = self._meter.create_rectangle(0, 0, 0, 6, fill=BACKGROUND, width=0)

        self._build_buttons(outer)
        for widget in (outer, top, self._status, self._dot, self._timer):
            self._bind_drag(widget)

    def _build_buttons(self, parent: tk.Frame) -> None:
        row = tk.Frame(parent, bg=BACKGROUND)
        row.pack(fill="x", padx=8, pady=(0, 8))

        # Buttons size to their own text rather than to a character count: Georgian
        # glyphs are wider than Latin ones, so a fixed width in characters clips the label.
        self._record_button = self._make_button(row, "● ჩაწერა", self._controller.toggle_recording)
        self._pause_button = self._make_button(row, "❚❚ პაუზა", self._controller.toggle_pause)
        self._cancel_button = self._make_button(row, "✕", self._controller.cancel_recording, 6)
        self._power_button = self._make_button(row, "⏻", self._controller.toggle_enabled, 6)

    def _make_button(
        self, parent: tk.Frame, label: str, command: Callable[[], None], padx: int = 10
    ) -> tk.Button:
        button = tk.Button(
            parent,
            text=label,
            command=lambda: self._safely(command),
            bg=BUTTON_BACKGROUND,
            fg=TEXT,
            activebackground=BUTTON_ACTIVE,
            activeforeground=TEXT,
            disabledforeground="#5a5a5e",
            relief="flat",
            borderwidth=0,
            padx=padx,
            pady=3,
            font=("Segoe UI", 9),
            cursor="hand2",
            takefocus=False,
        )
        button.pack(side="left", padx=2)
        return button

    def _safely(self, command: Callable[[], None]) -> None:
        """A button must never be able to take the window down with it."""
        try:
            command()
        except Exception:
            logger.exception("a window button raised")

    # -------------------------------------------------------------------------- dragging

    def _bind_drag(self, widget: tk.Misc) -> None:
        widget.bind("<Button-1>", self._on_drag_start)
        widget.bind("<B1-Motion>", self._on_drag)
        widget.bind("<ButtonRelease-1>", self._on_drag_end)

    def _on_drag_start(self, event: tk.Event) -> None:
        self._drag_origin = (
            event.x_root - self._root.winfo_x(),
            event.y_root - self._root.winfo_y(),
        )

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        offset_x, offset_y = self._drag_origin
        self._root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _on_drag_end(self, _event: tk.Event) -> None:
        self._drag_origin = None
        self._save_position()

    # -------------------------------------------------------------------------- position

    def _restore_position(self) -> tuple[int, int]:
        """Put the window back where the user left it, if that is still on screen."""
        screen_width = self._root.winfo_screenwidth()
        screen_height = self._root.winfo_screenheight()
        default = (
            screen_width - WINDOW_WIDTH - EDGE_MARGIN,
            screen_height - WINDOW_HEIGHT - TASKBAR_ALLOWANCE,
        )
        try:
            saved = json.loads(self._position_path.read_text(encoding="utf-8"))
            x, y = int(saved["x"]), int(saved["y"])
        except (OSError, ValueError, KeyError, TypeError):
            return default

        on_screen_x = -WINDOW_WIDTH + EDGE_MARGIN < x < screen_width - EDGE_MARGIN
        on_screen_y = -EDGE_MARGIN < y < screen_height - EDGE_MARGIN
        return (x, y) if on_screen_x and on_screen_y else default

    def _save_position(self) -> None:
        try:
            self._position_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"x": self._root.winfo_x(), "y": self._root.winfo_y()}
            self._position_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            logger.warning("could not remember the window position: %s", exc)

    # -------------------------------------------------------------------------- painting

    def _refresh(self) -> None:
        """The one place Tk is touched on a schedule. Also how a close from another
        thread reaches the Tk thread — `close` only sets a flag, because calling into Tk
        from outside its own thread is how Tk applications die mysteriously."""
        if self._closing:
            self._destroy()
            return
        try:
            self._paint()
        except Exception:
            logger.exception("painting the window failed")
        self._root.after(REFRESH_MS, self._refresh)

    def _paint(self) -> None:
        state = self._controller.ui_state()
        colour, words = APPEARANCE.get(state, APPEARANCE["idle"])

        self._dot.itemconfig(self._dot_id, fill=colour)
        self._status.config(text=words, fg=DIM_TEXT if state == "disabled" else TEXT)
        self._timer.config(text=_format_elapsed(self._controller.ui_elapsed_seconds()))

        width = max(0, int(self._meter.winfo_width() * self._controller.ui_level()))
        self._meter.coords(self._meter_bar, 0, 0, width, 6)
        self._meter.itemconfig(self._meter_bar, fill=colour)

        self._paint_buttons(state)

    def _paint_buttons(self, state: str) -> None:
        busy = state in ("recording", "paused")

        self._hotkey_hint.config(text="" if busy else self._controller.ui_hotkey_label())
        self._record_button.config(
            text="■ გაჩერება" if busy else "● ჩაწერა",
            state="disabled" if state in ("transcribing", "disabled") else "normal",
        )
        self._pause_button.config(
            text="▶ გაგრძელება" if state == "paused" else "❚❚ პაუზა",
            state="normal" if busy else "disabled",
        )
        self._cancel_button.config(state="normal" if busy else "disabled")
        self._power_button.config(fg=DIM_TEXT if state == "disabled" else TEXT)

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

    def _on_quit(self) -> None:
        self._safely(self._controller.quit)
