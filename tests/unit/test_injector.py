"""Clipboard handling — no real clipboard, no real keystrokes.

The distinction these tests protect: when the clipboard write fails the text is nowhere,
and telling the user to press Ctrl+V would send them after nothing.
"""

import contextlib

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

    assert keyboard.pressed_keys == ["v"]
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

    assert keyboard.pressed_keys == ["v"]
    assert clipboard.content == TRANSCRIPT  # nothing to restore, so the text stays


def test_restore_can_be_switched_off(clipboard):
    keyboard = FakeKeyboard()
    inject_text(TRANSCRIPT, restore_clipboard=False, restore_delay_ms=0, keyboard=keyboard)

    assert clipboard.writes == [TRANSCRIPT]
    assert clipboard.content == TRANSCRIPT


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

    assert keyboard.pressed_keys == ["v"]
