"""The per-system layer.

Only one branch of each function can run on the machine the tests run on, so the rest are
driven by pretending to be the other system. That is the point of the module: the choice
is one flag, in one place, and everything else reads it.
"""

from pathlib import Path

import pytest

from voice_typer import platform_support


@pytest.fixture
def pretend(monkeypatch):
    """Run the body as though we were on a named system."""

    def choose(system: str) -> None:
        monkeypatch.setattr(platform_support, "IS_WINDOWS", system == "windows")
        monkeypatch.setattr(platform_support, "IS_MACOS", system == "macos")

    return choose


def test_exactly_one_system_is_claimed():
    assert not (platform_support.IS_WINDOWS and platform_support.IS_MACOS)


def test_the_tray_is_only_offered_where_it_can_run(pretend):
    """On macOS the status bar wants the main thread, which Tk already has."""
    pretend("windows")
    assert platform_support.tray_is_supported()

    pretend("macos")
    assert not platform_support.tray_is_supported()


def test_a_folder_is_opened_with_the_system_command(pretend, monkeypatch):
    pretend("macos")
    calls = []
    monkeypatch.setattr(platform_support.subprocess, "run", lambda *a, **k: calls.append(a[0]))

    folder = Path("/tmp/somewhere")
    platform_support.open_path(folder)

    assert calls == [["/usr/bin/open", str(folder)]]


def test_a_folder_that_will_not_open_does_not_take_the_app_down(pretend, monkeypatch):
    pretend("macos")

    def refuse(*_args, **_kwargs):
        raise OSError("no such command")

    monkeypatch.setattr(platform_support.subprocess, "run", refuse)
    platform_support.open_path(Path("/tmp/somewhere"))  # must not raise


def test_quotes_in_a_message_cannot_break_out_of_the_applescript():
    """A message is built into a script, so anything quote-shaped has to be neutralised."""
    escaped = platform_support._escape_for_applescript('he said "no" \\ then left')
    assert '\\"no\\"' in escaped
    assert "\\\\" in escaped


def test_a_message_reaches_the_user_through_applescript(pretend, monkeypatch):
    pretend("macos")
    scripts = []
    monkeypatch.setattr(
        platform_support.subprocess, "run", lambda args, **_k: scripts.append(args[-1])
    )

    platform_support.show_dialog("ვერ გაეშვა")

    assert len(scripts) == 1
    assert "ვერ გაეშვა" in scripts[0]
    assert "voice-typer" in scripts[0]


def test_a_message_still_lands_somewhere_when_no_dialog_is_possible(pretend, capsys):
    """Silence is the one unacceptable outcome — the app would just never appear."""
    pretend("linux")

    platform_support.show_dialog("something went wrong")

    assert "something went wrong" in capsys.readouterr().err


def test_a_dialog_that_fails_does_not_raise(pretend, monkeypatch, capsys):
    pretend("macos")

    def refuse(*_args, **_kwargs):
        raise OSError("osascript is missing")

    monkeypatch.setattr(platform_support.subprocess, "run", refuse)
    platform_support.show_dialog("something went wrong")

    assert "something went wrong" in capsys.readouterr().err


def test_the_windows_only_startup_chores_do_nothing_elsewhere(pretend):
    pretend("macos")
    platform_support.make_dpi_aware()  # must not raise
    platform_support.hide_own_console()  # must not raise
