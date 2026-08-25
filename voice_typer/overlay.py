"""The recorder window — the app's whole visible surface.

A tray icon was not enough: Windows 11 hides new tray icons behind the "^" arrow by
default, so the only status indicator the app had was invisible until the user went
looking for it.

The window is frameless, always on top, and draggable. Three things about it are
load-bearing rather than cosmetic:

* **It never takes focus.** `window_platform` marks the real window non-activating, so
  clicking a button here does not move focus away from whatever the user was typing into.
  Even so, Windows can still shift focus on a click, which is why `app.py` remembers the
  window the user was working in and `injector.py` hands focus back before pasting.
* **It only reads.** Every value on screen is polled from the app; the window owns no
  state of its own. That keeps it safe to update from the Tk thread while recording,
  transcription, and timers run on three others.
* **It does not draw.** Not one canvas call is made here. `card_painter` draws and hands
  back a record of everything it made; this file owns the root, the canvas widget, the
  pointer, and the user's remembered choices, and nothing else.

The rest of the card lives next door: `card_layout` for measurements and colours,
`card_painter` and `card_buttons` for the drawing, `card_menu` for the right-click menu,
`window_state` for where the card was left.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from voice_typer import tcl_paths

logger = logging.getLogger(__name__)


tcl_paths.point_at_the_base_installation()

import tkinter as tk  # noqa: E402 — must follow the Tcl path fix above

from voice_typer import (  # noqa: E402
    card_buttons,
    card_menu,
    card_painter,
    window_platform,
    window_state,
)
from voice_typer import card_layout as layout  # noqa: E402
from voice_typer import widget_theme as theme  # noqa: E402
from voice_typer.window_platform import STANDARD_DPI  # noqa: E402


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
        # Everything the canvas hands out lives in one record, replaced whole whenever
        # the drawing is thrown away. See `card_layout.Card` for why that matters.
        self._card = layout.Card()
        # The display's own scaling, then the user's preference on top of it. Both go
        # through the same multiplier, so a smaller window is drawn small rather than
        # drawn large and shrunk — which is what would make it soft again. Text and icons
        # carry a second factor, so lettering stays readable at a card size that would
        # otherwise make it squint-small.
        self._scale = window_platform.display_scale() * window_scale
        self._m = layout.Metrics(self._scale, self._scale * content_scale)

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
            width=self._m.s(self._design_width),
            height=self._m.s(self._design_height),
            highlightthickness=0,
            bg=self._backdrop,
        )
        self._canvas.pack(fill="both", expand=True)
        self._repaint()
        self._build_menu()
        self._bind_events()
        self._root.update()
        window_platform.make_non_activating(self._root)
        # After `update`, because there is no NSWindow to configure until Tk has made one.
        window_platform.float_over_full_screen(self._root)
        self._refresh()

    # ------------------------------------------------------------------------ measuring

    @property
    def _design_width(self) -> int:
        """The card's width in design pixels, for whichever shape it is currently wearing."""
        return self._m.card_width(folded=self._collapsed, rewrite=self._rewrite_mode)

    @property
    def _design_height(self) -> int:
        return self._m.card_height(folded=self._collapsed)

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
        self._root.geometry(
            f"{self._m.s(self._design_width)}x{self._m.s(self._design_height)}+{x}+{y}"
        )
        self._root.protocol("WM_DELETE_WINDOW", lambda: self._safely(self._controller.quit))

    # ------------------------------------------------------------------------- painting

    # ------------------------------------------------------------------------- painting

    def _repaint(self) -> None:
        """Draw the card and take ownership of everything the canvas handed out.

        The only place a `Card` is made. Every other reference to `self._card` is a read
        of the drawing that is presently on the canvas, which is why clearing the canvas
        and calling this must always happen together.
        """
        self._card = card_painter.paint(
            self._canvas,
            self._m,
            self._actions(),
            folded=self._collapsed,
            rewrite=self._rewrite_mode,
        )

    def _actions(self) -> card_buttons.Actions:
        """What the drawn controls do. The window is the only object holding both the app
        and the canvas, so it is where the two are introduced to each other."""
        return card_buttons.Actions(
            toggle_recording=self._controller.toggle_recording,
            toggle_pause=self._controller.toggle_pause,
            cancel_recording=self._controller.cancel_recording,
            quit=self._controller.quit,
            toggle_collapsed=self.toggle_collapsed,
            toggle_rewrite_mode=self.toggle_rewrite_mode,
        )

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
        to the drawing that is being deleted. Clearing the canvas and replacing the record
        happen in the same breath, so none of them can outlive the drawing: a stale id is
        not an error in Tk, it is a silent no-op, which is how a window ends up looking
        frozen. `_pressed` and `_drag_origin` are not drawing state — the canvas never
        handed them out — so they are reset separately.
        """
        self._canvas.delete("all")
        self._pressed = None
        self._drag_origin = None

        width = self._m.s(self._design_width)
        height = self._m.s(self._design_height)
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
        self._repaint()
        self._update()

    # -------------------------------------------------------------------------- events

    def _bind_events(self) -> None:
        self._canvas.bind("<Button-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Motion>", self._on_move)
        self._canvas.bind("<Leave>", lambda _e: self._set_hover(None))
        self._canvas.bind("<Button-3>", self._show_menu)

    def _on_press(self, event: tk.Event) -> None:
        self._pressed = card_buttons.at(self._card, event.x, event.y)
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

        name = card_buttons.at(self._card, event.x, event.y)
        pressed, self._pressed = self._pressed, None
        if name is not None and name == pressed:
            self._safely(self._card.buttons[name].command)

    def _on_move(self, event: tk.Event) -> None:
        self._set_hover(card_buttons.at(self._card, event.x, event.y))

    def _set_hover(self, name: str | None) -> None:
        card_buttons.set_hover(self._canvas, self._card, name)

    def _button_actions(self) -> card_buttons.Actions:
        """The four commands the drawn buttons carry. The window is the only object that
        holds both the app and the canvas, so it is where the two are introduced."""
        return card_buttons.Actions(
            toggle_recording=self._controller.toggle_recording,
            toggle_pause=self._controller.toggle_pause,
            cancel_recording=self._controller.cancel_recording,
            quit=self._controller.quit,
        )

    def _safely(self, command: Callable[[], None]) -> None:
        """A button must never be able to take the window down with it."""
        try:
            command()
        except Exception:
            logger.exception("a window button raised")

    # ---------------------------------------------------------------------------- menu

    def _build_menu(self) -> None:
        """Assemble what the menu may ask for: some of it is the app's, some is ours.

        Every command goes through `_safely`, so a menu entry that raises leaves the
        window standing — the same guarantee the drawn buttons have.
        """
        self._menu, self._menu_index = card_menu.build(
            self._root,
            card_menu.Actions(
                toggle_enabled=lambda: self._safely(self._controller.toggle_enabled),
                retry_last=lambda: self._safely(self._controller.retry_last),
                copy_raw_text=lambda: self._safely(self._controller.copy_raw_text),
                toggle_rewrite_mode=lambda: self._safely(self.toggle_rewrite_mode),
                toggle_collapsed=lambda: self._safely(self.toggle_collapsed),
                open_logs=lambda: self._safely(self._controller.open_logs),
                open_settings=lambda: self._safely(self._controller.open_settings),
                open_rewrite_prompt=lambda: self._safely(self._controller.open_rewrite_prompt),
                open_keys=lambda: self._safely(self._open_keys),
                quit_app=lambda: self._safely(self._controller.quit),
            ),
        )

    def _open_keys(self) -> None:
        card_menu.open_keys(self._root, self._controller.reload_keys)

    def _sync_menu(self) -> None:
        card_menu.sync(
            self._menu,
            self._menu_index,
            usage=self._controller.usage_text(),
            listening=self._controller.ui_state() != "disabled",
            rewrite=self._rewrite_mode,
            folded=self._collapsed,
        )

    def _show_menu(self, event: tk.Event) -> None:
        self._sync_menu()
        card_menu.popup(self._menu, event.x_root, event.y_root)

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
        width, height = self._m.s(self._design_width), self._m.s(self._design_height)
        margin = self._m.s(layout.EDGE_MARGIN)
        return window_state.choose_position(
            window_state.read(self._position_path).position,
            default=(
                self._root.winfo_screenwidth() - width - margin,
                self._root.winfo_screenheight() - height - self._m.s(layout.TASKBAR_ALLOWANCE),
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
        self._root.after(layout.REFRESH_MS, self._refresh)

    def _update(self) -> None:
        """Ask the app everything, once, and hand the answers to the drawing.

        Every judgement is made here — which look the state wears, whether the level is
        worth showing — so nothing in `card_painter` has an opinion about state. This is
        also the only place the app is read on the Tk thread.
        """
        state = self._controller.ui_state()
        card_painter.show(
            self._canvas,
            self._m,
            self._card,
            card_painter.Frame(
                look=layout.APPEARANCE.get(state, layout.APPEARANCE["idle"]),
                state=state,
                timer=layout.format_elapsed(self._controller.ui_elapsed_seconds()),
                hotkey=self._controller.ui_hotkey_label(),
                notice=self._controller.ui_notice(),
                device=self._controller.ui_device_label(),
                # A level only means something while the microphone is open.
                level=self._controller.ui_level() if state == "recording" else 0.0,
            ),
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
