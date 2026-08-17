"""One key, two ways to use it.

Hold it down and it behaves like a walkie-talkie button. Tap it and recording latches on
until the next tap. Which one the user meant is decided by how long the key was held.

The decision logic lives in `HotkeyLogic`, which touches no hardware and takes its own
clock as an argument — so the timing rules can be tested without a keyboard.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from enum import Enum, auto

from pynput import keyboard

logger = logging.getLogger(__name__)


class Action(Enum):
    """What the state machine in `app.py` should do next."""

    NONE = auto()
    START = auto()
    STOP = auto()
    CANCEL = auto()


class _State(Enum):
    IDLE = auto()  # not recording
    HOLDING = auto()  # recording, key still down — push-to-talk so far
    LATCHED = auto()  # recording, key released after a short tap — waiting for the next tap
    AWAIT_RELEASE = auto()  # recording already stopped, key still physically down


class HotkeyLogic:
    """Turns raw press and release events into start/stop/cancel decisions."""

    def __init__(self, hold_threshold_ms: int) -> None:
        self._hold_threshold_ms = hold_threshold_ms
        self._state = _State.IDLE
        self._pressed_at_ms: float = 0.0

    @property
    def is_recording(self) -> bool:
        return self._state in (_State.HOLDING, _State.LATCHED)

    def on_press(self, now_ms: float) -> Action:
        """The hotkey went down."""
        if self._state is _State.IDLE:
            self._pressed_at_ms = now_ms
            self._state = _State.HOLDING
            return Action.START

        if self._state is _State.LATCHED:
            # Second tap of a toggle — stop straight away rather than waiting for release,
            # so the key feels responsive.
            self._state = _State.AWAIT_RELEASE
            return Action.STOP

        return Action.NONE

    def on_release(self, now_ms: float) -> Action:
        """The hotkey came up."""
        if self._state is _State.HOLDING:
            held_ms = now_ms - self._pressed_at_ms
            if held_ms >= self._hold_threshold_ms:
                self._state = _State.IDLE
                return Action.STOP
            self._state = _State.LATCHED  # too short to be a hold — treat it as a toggle
            return Action.NONE

        if self._state is _State.AWAIT_RELEASE:
            self._state = _State.IDLE

        return Action.NONE

    def on_escape(self) -> Action:
        """Escape was pressed. Discards the recording only when one is in progress."""
        if self._state is _State.HOLDING:
            self._state = _State.AWAIT_RELEASE  # the hotkey is still down; swallow its release
            return Action.CANCEL

        if self._state is _State.LATCHED:
            self._state = _State.IDLE
            return Action.CANCEL

        return Action.NONE

    def force_idle(self) -> None:
        """Reset after the app stopped recording on its own — the auto-stop ceiling, or an error."""
        self._state = _State.AWAIT_RELEASE if self._state is _State.HOLDING else _State.IDLE


def parse_key(name: str) -> keyboard.Key | keyboard.KeyCode:
    """Turn a config value such as 'f9', 'ctrl_r' or 'caps_lock' into a pynput key."""
    cleaned = name.strip().lower()
    if cleaned in keyboard.Key.__members__:
        return keyboard.Key[cleaned]
    if len(cleaned) == 1:
        return keyboard.KeyCode.from_char(cleaned)
    raise ValueError(
        f"'{name}' is not a key this app recognises. Try f9, f2, ctrl_r, alt_r or caps_lock."
    )


def _same_key(pressed: object, wanted: keyboard.Key | keyboard.KeyCode) -> bool:
    """Compare two pynput keys, ignoring letter case for character keys."""
    if pressed == wanted:
        return True
    pressed_char = getattr(pressed, "char", None)
    wanted_char = getattr(wanted, "char", None)
    return bool(pressed_char and wanted_char and pressed_char.lower() == wanted_char.lower())


class HotkeyListener:
    """Watches the keyboard system-wide and reports what the user meant."""

    def __init__(
        self,
        hotkey: str,
        hold_threshold_ms: int,
        on_action: Callable[[Action], None],
    ) -> None:
        self._key = parse_key(hotkey)
        self._logic = HotkeyLogic(hold_threshold_ms)
        self._on_action = on_action
        self._listener: keyboard.Listener | None = None

    @property
    def logic(self) -> HotkeyLogic:
        """Exposed so the app can reset the state after an auto-stop."""
        return self._logic

    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press=self._handle_press, on_release=self._handle_release
        )
        self._listener.start()
        logger.info("listening for the hotkey")

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            logger.info("stopped listening for the hotkey")

    def _handle_press(self, key: object) -> None:
        """Everything here is wrapped: pynput stops the listener on an uncaught exception,
        and a dead listener looks exactly like a working one — the app sits in the tray and
        the hotkey silently does nothing."""
        try:
            if _same_key(key, self._key):
                self._dispatch(self._logic.on_press(time.monotonic() * 1000))
            elif key == keyboard.Key.esc and self._logic.is_recording:
                self._dispatch(self._logic.on_escape())
        except Exception:
            logger.exception("key press handling raised — the listener stays alive")

    def _handle_release(self, key: object) -> None:
        try:
            if _same_key(key, self._key):
                self._dispatch(self._logic.on_release(time.monotonic() * 1000))
        except Exception:
            logger.exception("key release handling raised — the listener stays alive")

    def _dispatch(self, action: Action) -> None:
        """Never let a callback exception kill the listener thread."""
        if action is Action.NONE:
            return
        try:
            self._on_action(action)
        except Exception:
            logger.exception("the hotkey handler raised — the listener stays alive")
