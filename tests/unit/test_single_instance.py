"""One copy at a time.

Two copies would both hear F9, both record, both pay, and both paste — the user would get
everything twice and be billed twice.
"""

from voice_typer.single_instance import SingleInstance

NAME = "Local\\voice-typer-test-lock"


def test_the_first_copy_gets_the_lock():
    first = SingleInstance(NAME)
    try:
        assert first.acquire()
    finally:
        first.release()


def test_a_second_copy_is_turned_away():
    first = SingleInstance(NAME)
    second = SingleInstance(NAME)
    try:
        assert first.acquire()
        assert not second.acquire()
    finally:
        first.release()
        second.release()


def test_the_lock_is_free_again_once_the_first_copy_lets_go():
    first = SingleInstance(NAME)
    assert first.acquire()
    first.release()

    second = SingleInstance(NAME)
    try:
        assert second.acquire()
    finally:
        second.release()


def test_releasing_twice_is_harmless():
    lock = SingleInstance(NAME)
    lock.acquire()
    lock.release()
    lock.release()  # must not raise


def test_releasing_without_acquiring_is_harmless():
    SingleInstance(NAME).release()


def test_a_broken_lock_lets_the_app_start_anyway(monkeypatch):
    """Refusing to start because the lock could not be taken would be worse than the
    duplicate it is meant to prevent."""
    import ctypes

    monkeypatch.setattr(
        ctypes, "windll", property(lambda _self: (_ for _ in ()).throw(OSError("no windll")))
    )
    assert SingleInstance(NAME).acquire()
