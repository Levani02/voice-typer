"""Puts the transcript into whatever window has focus.

The text goes via the clipboard and Ctrl+V rather than synthesised keystrokes: Georgian
characters cannot be typed reliably that way, because the result depends on the active
keyboard layout. Paste bypasses layout entirely.

Whatever the user had copied before is saved and put back afterwards, including when the
paste itself fails.
"""

from __future__ import annotations

import ctypes
import logging
import time

import pyperclip
from pynput.keyboard import Controller, Key, KeyCode

logger = logging.getLogger(__name__)

# The paste key, addressed by virtual-key code rather than by the character "v".
#
# This is not a nicety. pynput resolves a character through VkKeyScanW against the calling
# thread's active keyboard layout — and the Georgian layout has no Latin "v", so the lookup
# fails and pynput falls back to KEYEVENTF_UNICODE, which Windows delivers as VK_PACKET
# with wVk = 0. Ctrl + VK_PACKET matches no paste accelerator, yet SendInput still reports
# success, so the app would believe it had pasted, delete the recording, and restore the
# old clipboard over the transcript. Measured on this machine:
# VkKeyScanExW('v', layout 0x0437) = -1, while layout 0x0409 gives 86.
#
# 0x56 is VK_V, which means the same physical key on every layout.
PASTE_KEY = KeyCode.from_vk(0x56)

# Virtual-key codes for the modifiers that would corrupt a synthetic Ctrl+V if still held.
_MODIFIER_VK_CODES = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # shift, ctrl, alt, left win, right win
_KEY_DOWN_MASK = 0x8000

MODIFIER_WAIT_TIMEOUT_SECONDS = 1.0
MODIFIER_POLL_INTERVAL_SECONDS = 0.02


class InjectionError(Exception):
    """The text could not be delivered. The message is shown to the user."""


class ClipboardUnavailableError(InjectionError):
    """The text never reached the clipboard, so pressing Ctrl+V would not recover it.

    Kept separate from PasteFailedError because the two need opposite advice: telling
    someone to press Ctrl+V when the clipboard write failed sends them after nothing.
    """


class PasteFailedError(InjectionError):
    """The text is sitting on the clipboard; only the keystroke was refused."""


def _modifiers_are_down() -> bool:
    """True while any modifier key is physically held."""
    get_state = ctypes.windll.user32.GetAsyncKeyState  # type: ignore[attr-defined]
    return any(get_state(vk) & _KEY_DOWN_MASK for vk in _MODIFIER_VK_CODES)


def _wait_for_modifiers_released(timeout_seconds: float = MODIFIER_WAIT_TIMEOUT_SECONDS) -> None:
    """Block until no modifier is held, or until the timeout — then paste anyway.

    Giving up after the timeout is deliberate: a keyboard hook can occasionally miss a key
    release, and refusing to paste would lose the user's words over a cosmetic problem.
    """
    deadline = time.monotonic() + timeout_seconds
    while _modifiers_are_down():
        if time.monotonic() >= deadline:
            logger.warning(
                "a modifier key is still held after %.1fs — pasting anyway", timeout_seconds
            )
            return
        time.sleep(MODIFIER_POLL_INTERVAL_SECONDS)


def _read_clipboard() -> str | None:
    """Current clipboard text, or None if it holds something else (an image, say)."""
    try:
        return pyperclip.paste()
    except Exception as exc:
        logger.info("could not read the clipboard, so it will not be restored: %s", exc)
        return None


def _write_clipboard(text: str) -> None:
    """Put the text on the clipboard and confirm it actually took.

    pyperclip's Windows path can return without raising while another program holds the
    clipboard open, so the write is read back rather than assumed.
    """
    try:
        pyperclip.copy(text)
    except Exception as exc:
        raise ClipboardUnavailableError(f"could not write to the clipboard: {exc}") from exc

    try:
        written = pyperclip.paste()
    except Exception:
        return  # unreadable but possibly written — pasting is still worth attempting

    if written != text:
        raise ClipboardUnavailableError(
            "the clipboard did not take the text — another program is holding it"
        )


def _send_paste(keyboard: Controller) -> None:
    try:
        with keyboard.pressed(Key.ctrl):
            keyboard.press(PASTE_KEY)
            keyboard.release(PASTE_KEY)
    except Exception as exc:
        raise PasteFailedError(f"the paste keystroke was rejected: {exc}") from exc


def inject_text(
    text: str,
    *,
    restore_clipboard: bool = True,
    restore_delay_ms: int = 300,
    keyboard: Controller | None = None,
) -> None:
    """Paste `text` at the cursor in the focused window.

    On failure the text is left on the clipboard, so the user can still press Ctrl+V.
    """
    if not text:
        return

    keyboard = keyboard or Controller()
    previous = _read_clipboard() if restore_clipboard else None
    pasted = False

    try:
        _write_clipboard(text)
        _wait_for_modifiers_released()
        _send_paste(keyboard)
        pasted = True
        logger.info("pasted %d characters", len(text))
    finally:
        # Restore only after a paste that worked. If it did not, the transcript stays on
        # the clipboard — losing the user's words to a tidy clipboard would be worse.
        if previous is not None and pasted:
            time.sleep(restore_delay_ms / 1000)
            try:
                pyperclip.copy(previous)
            except Exception as exc:
                logger.warning("could not restore the previous clipboard contents: %s", exc)
