"""One copy at a time.

Two copies would both hear F9, both record, both pay, and both paste — the user would get
everything twice and be billed twice.

The lock is a named mutex on Windows and an `flock` elsewhere, so the tests are written
against the behaviour rather than against either mechanism. Both names are handed in: the
one that does not apply to this system is simply ignored.
"""

import pytest

from voice_typer.single_instance import SingleInstance

NAME = "Local\\voice-typer-test-lock"


@pytest.fixture
def make_lock(tmp_path):
    """A lock nothing else shares — never the real one under `logs/`."""

    def build() -> SingleInstance:
        return SingleInstance(NAME, tmp_path / "voice-typer-test.lock")

    return build


def test_the_first_copy_gets_the_lock(make_lock):
    first = make_lock()
    try:
        assert first.acquire()
    finally:
        first.release()


def test_a_second_copy_is_turned_away(make_lock):
    first, second = make_lock(), make_lock()
    try:
        assert first.acquire()
        assert not second.acquire()
    finally:
        first.release()
        second.release()


def test_the_lock_is_free_again_once_the_first_copy_lets_go(make_lock):
    first = make_lock()
    assert first.acquire()
    first.release()

    second = make_lock()
    try:
        assert second.acquire()
    finally:
        second.release()


def test_releasing_twice_is_harmless(make_lock):
    lock = make_lock()
    lock.acquire()
    lock.release()
    lock.release()  # must not raise


def test_releasing_without_acquiring_is_harmless(make_lock):
    make_lock().release()


def test_a_broken_lock_lets_the_app_start_anyway(make_lock, monkeypatch):
    """Refusing to start because the lock could not be taken would be worse than the
    duplicate it is meant to prevent."""

    def explode(_self) -> bool:
        raise OSError("the lock mechanism is unavailable")

    monkeypatch.setattr(SingleInstance, "_acquire_mutex", explode)
    monkeypatch.setattr(SingleInstance, "_acquire_flock", explode)

    assert make_lock().acquire()
