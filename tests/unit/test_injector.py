"""Clipboard handling — no real clipboard, no real keystrokes.

The distinction these tests protect: when the clipboard write fails the text is nowhere,
and telling the user to press Ctrl+V would send them after nothing.
"""

import contextlib
import time

import pytest

from voice_typer import injector as injector_module
from voice_typer.injector import (
    ClipboardUnavailableError,
    PasteFailedError,
    inject_text,
)

TRANSCRIPT = "გამარჯობა, ეს არის ტესტი"
PREVIOUS_CLIPBOARD = "something the user had copied earlier"


class FakeKeyboard:
    """Records the keystrokes it was asked to send."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.pressed_keys: list[str] = []

    @contextlib.contextmanager
    def pressed(self, _key):
        if self.fail:
            raise OSError("the target window refused the keystroke")
        yield

    def press(self, key):
        self.pressed_keys.append(key)

    def release(self, key):
        pass


class FakeClipboard:
    """Stands in for pyperclip. Can be made to fail on read, on write, or on both."""

    def __init__(self, initial=PREVIOUS_CLIPBOARD, fail_write=False, fail_read=False):
        self.content = initial
        self.fail_write = fail_write
        self.fail_read = fail_read
        self.writes: list[str] = []

    def copy(self, text):
        if self.fail_write:
            raise OSError("the clipboard is locked by another program")
        self.writes.append(text)
        self.content = text

    def paste(self):
        if self.fail_read:
            raise OSError("the clipboard holds something that is not text")
        return self.content


@pytest.fixture
def clipboard(monkeypatch):
    """Install a fake clipboard and skip the real modifier-key poll."""
    fake = FakeClipboard()
    monkeypatch.setattr(injector_module, "pyperclip", fake)
    monkeypatch.setattr(injector_module, "_wait_for_modifiers_released", lambda *a, **k: None)
    return fake


def test_the_text_is_pasted_and_the_old_clipboard_comes_back(clipboard):
    keyboard = FakeKeyboard()
    inject_text(TRANSCRIPT, restore_delay_ms=0, keyboard=keyboard)

    assert keyboard.pressed_keys == [injector_module.PASTE_KEY]
    assert clipboard.writes == [TRANSCRIPT, PREVIOUS_CLIPBOARD]
    assert clipboard.content == PREVIOUS_CLIPBOARD


def test_nothing_happens_for_empty_text(clipboard):
    keyboard = FakeKeyboard()
    inject_text("", keyboard=keyboard)

    assert keyboard.pressed_keys == []
    assert clipboard.writes == []


def test_a_failed_clipboard_write_never_claims_the_text_is_pasteable(clipboard, monkeypatch):
    """The whole point of the separate exception type."""
    monkeypatch.setattr(clipboard, "fail_write", True)
    keyboard = FakeKeyboard()

    with pytest.raises(ClipboardUnavailableError):
        inject_text(TRANSCRIPT, restore_delay_ms=0, keyboard=keyboard)

    assert keyboard.pressed_keys == []  # no keystroke was sent
    assert clipboard.content == PREVIOUS_CLIPBOARD  # and nothing was disturbed


def test_a_refused_keystroke_leaves_the_text_on_the_clipboard(clipboard):
    """Here Ctrl+V really would work, so the transcript must stay put."""
    keyboard = FakeKeyboard(fail=True)

    with pytest.raises(PasteFailedError):
        inject_text(TRANSCRIPT, restore_delay_ms=0, keyboard=keyboard)

    assert clipboard.content == TRANSCRIPT
    assert PREVIOUS_CLIPBOARD not in clipboard.writes  # the restore was correctly skipped


def test_the_two_failures_are_distinguishable_by_type(clipboard, monkeypatch):
    """App shows opposite advice for each, so they must never collapse into one."""
    assert issubclass(ClipboardUnavailableError, injector_module.InjectionError)
    assert issubclass(PasteFailedError, injector_module.InjectionError)
    assert not issubclass(ClipboardUnavailableError, PasteFailedError)


def test_an_unreadable_clipboard_does_not_stop_the_paste(clipboard, monkeypatch):
    """An image on the clipboard means it cannot be restored — that must not block dictation."""
    monkeypatch.setattr(clipboard, "fail_read", True)
    keyboard = FakeKeyboard()

    inject_text(TRANSCRIPT, restore_delay_ms=0, keyboard=keyboard)

    assert keyboard.pressed_keys == [injector_module.PASTE_KEY]
    assert clipboard.content == TRANSCRIPT  # nothing to restore, so the text stays


def test_restore_can_be_switched_off(clipboard):
    keyboard = FakeKeyboard()
    inject_text(TRANSCRIPT, restore_clipboard=False, restore_delay_ms=0, keyboard=keyboard)

    assert clipboard.writes == [TRANSCRIPT]
    assert clipboard.content == TRANSCRIPT


def test_the_paste_key_is_addressed_by_virtual_key_not_by_the_letter(clipboard):
    """Under the Georgian layout there is no Latin "v": pynput would fall back to
    KEYEVENTF_UNICODE, Windows would deliver VK_PACKET, and Ctrl+VK_PACKET pastes nothing
    while still reporting success — so the app would delete the recording for nothing."""
    key = injector_module.PASTE_KEY
    parameters = key._parameters(True)

    assert parameters["wVk"] == 0x56  # VK_V, the same physical key on every layout
    assert parameters["dwFlags"] == 0  # not 4 (KEYEVENTF_UNICODE)
    assert key.char is None  # nothing here goes through the layout at all


def test_the_modifier_wait_gives_up_rather_than_blocking_forever(monkeypatch):
    """A keyboard hook can miss a key-release. Waiting for ever would hang the app and
    lose the words; pasting with a modifier held is the lesser problem."""
    monkeypatch.setattr(injector_module, "_modifiers_are_down", lambda: True)
    monkeypatch.setattr(injector_module, "MODIFIER_POLL_INTERVAL_SECONDS", 0.001)

    started = time.monotonic()
    injector_module._wait_for_modifiers_released(timeout_seconds=0.05)
    elapsed = time.monotonic() - started

    assert 0.05 <= elapsed < 1.0


def test_the_modifier_wait_returns_as_soon_as_the_keys_come_up(monkeypatch):
    states = iter([True, True, False])
    monkeypatch.setattr(injector_module, "_modifiers_are_down", lambda: next(states, False))
    monkeypatch.setattr(injector_module, "MODIFIER_POLL_INTERVAL_SECONDS", 0.001)

    started = time.monotonic()
    injector_module._wait_for_modifiers_released(timeout_seconds=5.0)

    assert time.monotonic() - started < 1.0  # it did not wait out the timeout


def test_a_clipboard_that_silently_refuses_the_write_is_caught(clipboard, monkeypatch):
    """pyperclip's Windows path can return without raising while another program holds
    the clipboard. Believing that write would cost the user their words."""
    monkeypatch.setattr(clipboard, "copy", lambda _text: None)  # accepts, changes nothing
    keyboard = FakeKeyboard()

    with pytest.raises(ClipboardUnavailableError, match="did not take"):
        inject_text(TRANSCRIPT, restore_delay_ms=0, keyboard=keyboard)

    assert keyboard.pressed_keys == []


def test_a_failure_to_restore_is_not_reported_as_a_failed_paste(clipboard, monkeypatch):
    """The words are in the window; a clipboard that will not go back is not a failure."""
    keyboard = FakeKeyboard()
    calls = {"n": 0}

    def copy_then_break(text):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("clipboard closed")
        clipboard.content = text

    monkeypatch.setattr(clipboard, "copy", copy_then_break)

    inject_text(TRANSCRIPT, restore_delay_ms=0, keyboard=keyboard)  # must not raise

    assert keyboard.pressed_keys == [injector_module.PASTE_KEY]
