"""The icon that lands on the desktop.

A downloaded executable sits wherever the browser put it. Without this, the second run
means remembering where that was — so the shortcut is the difference between a tool that
gets used and one that gets lost.

Nothing here may raise: an app that refuses to start because it could not decorate the
desktop is worse than one with no icon.
"""

from pathlib import Path

import pytest

from voice_typer import desktop_shortcut


@pytest.fixture
def pretend(monkeypatch):
    def choose(system: str) -> None:
        monkeypatch.setattr(desktop_shortcut, "IS_WINDOWS", system == "windows")

    return choose


@pytest.fixture
def desktop(tmp_path):
    folder = tmp_path / "Desktop"
    folder.mkdir()
    return folder


@pytest.fixture
def needs_symlinks(tmp_path):
    """The macOS path makes a symlink, which Windows refuses without a privilege.

    Skipped rather than faked: a mocked symlink would prove nothing, and the macOS runner
    in CI executes these for real.
    """
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(tmp_path)
    except OSError as exc:
        pytest.skip(f"this system will not create symlinks: {exc}")
    probe.unlink()


def test_a_packaged_mac_app_is_linked_by_its_own_name(
    needs_symlinks, pretend, monkeypatch, tmp_path, desktop
):
    """Finder shows the link with the application's icon, which is the whole point."""
    pretend("macos")
    bundle = tmp_path / "voice-typer.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    executable = bundle / "Contents" / "MacOS" / "voice-typer"
    executable.write_text("", encoding="utf-8")

    monkeypatch.setattr(desktop_shortcut.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_shortcut.sys, "executable", str(executable))

    link = desktop_shortcut.create_desktop_shortcut(desktop)

    assert link == desktop / "voice-typer.app"
    assert link.is_symlink()
    assert link.resolve() == bundle.resolve()


def test_running_from_source_points_at_the_launcher(
    needs_symlinks, pretend, monkeypatch, tmp_path, desktop
):
    pretend("macos")
    project = tmp_path / "project"
    project.mkdir()
    launcher = project / "run.command"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.delattr(desktop_shortcut.sys, "frozen", raising=False)
    monkeypatch.setattr(desktop_shortcut, "PROJECT_ROOT", project)

    link = desktop_shortcut.create_desktop_shortcut(desktop)

    assert link == desktop / "voice-typer.command"
    assert link.resolve() == launcher.resolve()


def test_running_it_twice_replaces_the_link_rather_than_failing(
    needs_symlinks, pretend, monkeypatch, tmp_path, desktop
):
    pretend("macos")
    project = tmp_path / "project"
    project.mkdir()
    (project / "run.command").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.delattr(desktop_shortcut.sys, "frozen", raising=False)
    monkeypatch.setattr(desktop_shortcut, "PROJECT_ROOT", project)

    first = desktop_shortcut.create_desktop_shortcut(desktop)
    second = desktop_shortcut.create_desktop_shortcut(desktop)

    assert first == second
    assert second.is_symlink()


def test_nothing_happens_when_there_is_nothing_to_point_at(pretend, monkeypatch, tmp_path, desktop):
    """From source without a launcher there is no single file a double-click could open."""
    pretend("macos")
    monkeypatch.delattr(desktop_shortcut.sys, "frozen", raising=False)
    monkeypatch.setattr(desktop_shortcut, "PROJECT_ROOT", tmp_path / "empty")

    assert desktop_shortcut.create_desktop_shortcut(desktop) is None


def test_a_missing_desktop_folder_is_not_an_error(pretend, monkeypatch, tmp_path):
    pretend("macos")
    project = tmp_path / "project"
    project.mkdir()
    (project / "run.command").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.delattr(desktop_shortcut.sys, "frozen", raising=False)
    monkeypatch.setattr(desktop_shortcut, "PROJECT_ROOT", project)

    assert desktop_shortcut.create_desktop_shortcut(tmp_path / "no-desktop-here") is None


def test_a_refusal_from_the_system_never_reaches_the_caller(pretend, monkeypatch, tmp_path):
    """The app must still start when the desktop cannot be written to."""
    pretend("macos")
    project = tmp_path / "project"
    project.mkdir()
    (project / "run.command").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.delattr(desktop_shortcut.sys, "frozen", raising=False)
    monkeypatch.setattr(desktop_shortcut, "PROJECT_ROOT", project)

    def refuse(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(desktop_shortcut, "_create_macos_alias", refuse)

    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    assert desktop_shortcut.create_desktop_shortcut(desktop) is None


def test_the_windows_shortcut_is_built_through_the_shell(pretend, monkeypatch, tmp_path):
    """A .lnk is a binary only the shell knows how to write, so PowerShell is asked."""
    pretend("windows")
    target = tmp_path / "voice-typer.exe"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr(desktop_shortcut.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_shortcut.sys, "executable", str(target))

    captured = {}

    class Result:
        returncode = 0
        stdout = "C:\\Users\\someone\\Desktop\\voice-typer.lnk\n"
        stderr = ""

    def fake_run(command, **_kwargs):
        captured["command"] = command
        return Result()

    monkeypatch.setattr(desktop_shortcut.subprocess, "run", fake_run)

    link = desktop_shortcut.create_desktop_shortcut()

    assert link == Path("C:\\Users\\someone\\Desktop\\voice-typer.lnk")
    script = captured["command"][-1]
    assert "GetFolderPath('Desktop')" in script  # never a guessed path — OneDrive moves it
    assert str(target) in script


def test_a_failing_shell_is_reported_as_no_shortcut(pretend, monkeypatch, tmp_path):
    pretend("windows")
    target = tmp_path / "voice-typer.exe"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr(desktop_shortcut.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_shortcut.sys, "executable", str(target))

    class Failure:
        returncode = 1
        stdout = ""
        stderr = "access is denied"

    monkeypatch.setattr(desktop_shortcut.subprocess, "run", lambda *a, **k: Failure())

    assert desktop_shortcut.create_desktop_shortcut() is None


def test_a_quote_in_a_path_cannot_break_the_script():
    """PowerShell escapes a single quote by doubling it, and nothing else."""
    quoted = desktop_shortcut._quote_for_powershell("C:\\it's here\\app.exe")
    assert quoted == "'C:\\it''s here\\app.exe'"
