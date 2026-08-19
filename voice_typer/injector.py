"""Puts the transcript into whatever window has focus.

The text goes via the clipboard and the system's paste shortcut rather than synthesised
keystrokes: Georgian characters cannot be typed reliably that way, because the result
depends on the active keyboard layout. Paste bypasses layout entirely.

Whatever the user had copied before is saved and put back afterwards, including when the
paste itself fails.

On macOS the keystroke is posted to Quartz directly instead of through pynput. Creating
pynput's keyboard controller asks Carbon for the current keyboard layout, and on macOS 15
that call insists on running on the main queue: from the worker thread that does the
pasting it does not raise, it kills the whole process with SIGTRAP. The key code is known
here already, so the layout lookup was never buying anything.
"""

from __future__ import annotations

import logging
import time

import pyperclip
from pynput.keyboard import Controller, Key, KeyCode

from voice_typer.focus import return_focus_to
from voice_typer.platform_support import IS_MACOS, IS_WINDOWS

logger = logging.getLogger(__name__)

# The paste key, addressed by its hardware key code rather than by the character "v".
#
# This is not a nicety. Asked for a character, pynput looks it up in the active keyboard
# layout — and the Georgian layout has no Latin "v". On Windows the lookup fails and
# pynput falls back to KEYEVENTF_UNICODE, which arrives as VK_PACKET with wVk = 0.
# Ctrl + VK_PACKET matches no paste accelerator, yet SendInput still reports success, so
# the app would believe it had pasted, delete the recording, and restore the old clipboard
# over the transcript. Measured on this machine:
# VkKeyScanExW('v', layout 0x0437) = -1, while layout 0x0409 gives 86.
#
# A key code means the same physical key whatever the layout says is printed on it:
# 0x56 is VK_V on Windows, 0x09 is kVK_ANSI_V on macOS.
_WINDOWS_VK_V = 0x56
_MACOS_VK_V = 0x09

PASTE_KEY = KeyCode.from_vk(_MACOS_VK_V if IS_MACOS else _WINDOWS_VK_V)

# macOS pastes with Command, everything else with Control.
PASTE_MODIFIER = Key.cmd if IS_MACOS else Key.ctrl

# What to call that shortcut when telling the user to press it themselves.
PASTE_SHORTCUT_LABEL = "Cmd+V" if IS_MACOS else "Ctrl+V"

# Virtual-key codes for the modifiers that would corrupt a synthetic paste if still held.
_MODIFIER_VK_CODES = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # shift, ctrl, alt, left win, right win
_KEY_DOWN_MASK = 0x8000

# The same modifiers as a macOS event-flags mask: shift, control, option, command.
_MACOS_MODIFIER_FLAGS = 0x20000 | 0x40000 | 0x80000 | 0x100000

MODIFIER_WAIT_TIMEOUT_SECONDS = 1.0
MODIFIER_POLL_INTERVAL_SECONDS = 0.02

# kVK_Command. Posted as its own key event so the receiving application sees the modifier
# go down before the "v" arrives, exactly as it would from a real keyboard.
_MACOS_VK_COMMAND = 0x37

# A breath between the four macOS key events. Posted back to back, an application that is
# still bringing up its window can miss the modifier and receive a bare "v".
MACOS_KEY_EVENT_GAP_SECONDS = 0.01


class InjectionError(Exception):
    """The text could not be delivered. The message is shown to the user."""


class ClipboardUnavailableError(InjectionError):
    """The text never reached the clipboard, so pressing Ctrl+V would not recover it.

    Kept separate from PasteFailedError because the two need opposite advice: telling
    someone to press Ctrl+V when the clipboard write failed sends them after nothing.
    """


class PasteFailedError(InjectionError):
    """The text is sitting on the clipboard; only the keystroke was refused."""


def _windows_modifiers_are_down() -> bool:
    import ctypes

    get_state = ctypes.windll.user32.GetAsyncKeyState  # type: ignore[attr-defined]
    return any(get_state(vk) & _KEY_DOWN_MASK for vk in _MODIFIER_VK_CODES)


def _macos_modifiers_are_down() -> bool:
    """Quartz reports the modifier flags the hardware is holding right now.

    pynput already brings Quartz in on macOS, so this costs no extra dependency. If the
    import ever fails the paste goes ahead unguarded, which is the same outcome as the
    timeout below.
    """
    import Quartz

    flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateHIDSystemState)
    return bool(flags & _MACOS_MODIFIER_FLAGS)


def _modifiers_are_down() -> bool:
    """True while any modifier key is physically held."""
    try:
        if IS_WINDOWS:
            return _windows_modifiers_are_down()
        if IS_MACOS:
            return _macos_modifiers_are_down()
    except Exception as exc:
        logger.debug("could not read the modifier keys: %s", exc)
    return False


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


def _load_quartz():
    """Imported through a function so a test can stand in for the real framework."""
    import Quartz

    return Quartz


def _macos_paste_is_permitted() -> bool:
    """Whether macOS will actually deliver a synthetic keystroke to another application.

    Without Accessibility permission `CGEventPost` succeeds and does nothing at all. That
    silence is the dangerous part: the app would count the paste as done, put the old
    clipboard back over the transcript and delete the recording, and the user's words
    would be gone with no error anywhere. Asking first turns that into an honest failure.

    If the check itself cannot run, the paste goes ahead — a missing answer is not a no.
    """
    try:
        import HIServices

        return bool(HIServices.AXIsProcessTrusted())
    except Exception as exc:
        logger.debug("could not ask macOS whether this app is trusted: %s", exc)
        return True


def _send_macos_paste() -> None:
    """Post Cmd+V as four Quartz events, without pynput anywhere in the path.

    See the module docstring: pynput's controller cannot be built off the main thread on
    macOS 15 without taking the process down with it.
    """
    if not _macos_paste_is_permitted():
        raise PasteFailedError(
            "macOS has not given this app Accessibility permission, "
            "so the paste keystroke cannot be sent"
        )

    try:
        quartz = _load_quartz()
    except Exception as exc:
        raise PasteFailedError(f"the macOS keyboard API is unavailable: {exc}") from exc

    command_held = quartz.kCGEventFlagMaskCommand
    strokes = (
        (_MACOS_VK_COMMAND, True, command_held),
        (_MACOS_VK_V, True, command_held),
        (_MACOS_VK_V, False, command_held),
        (_MACOS_VK_COMMAND, False, 0),
    )

    try:
        for key_code, is_press, flags in strokes:
            event = quartz.CGEventCreateKeyboardEvent(None, key_code, is_press)
            quartz.CGEventSetFlags(event, flags)
            quartz.CGEventPost(quartz.kCGHIDEventTap, event)
            time.sleep(MACOS_KEY_EVENT_GAP_SECONDS)
    except Exception as exc:
        raise PasteFailedError(f"the paste keystroke was rejected: {exc}") from exc


def _send_paste(keyboard: Controller | None = None) -> None:
    """Send the paste shortcut — through Quartz on macOS, through pynput everywhere else.

    `keyboard` is only ever supplied by a test; production code leaves it None so that
    the platform decides.
    """
    if keyboard is None and IS_MACOS:
        _send_macos_paste()
        return

    keyboard = keyboard or Controller()
    try:
        with keyboard.pressed(PASTE_MODIFIER):
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
    target_window: int = 0,
    started_from_our_window: bool = False,
) -> None:
    """Paste `text` at the cursor in the focused window.

    `target_window` is where the recording started. It is only used if our own window has
    the focus by then, which happens when the user clicks a button here instead of using
    the hotkey — which is what `started_from_our_window` records. If the focus cannot be
    given back, nothing is pasted at all: a keystroke sent into this app's own window
    would be counted as a success and would cost the user their words.

    On failure the text is left on the clipboard, so the user can still press Ctrl+V.
    """
    if not text:
        return

    previous = _read_clipboard() if restore_clipboard else None
    pasted = False

    try:
        _write_clipboard(text)
        if not return_focus_to(target_window, started_from_our_window=started_from_our_window):
            raise PasteFailedError("the window you were typing in did not come back to the front")
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
