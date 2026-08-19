"""Handing the focus back — no real windows, no real AppKit.

The distinction these tests protect: on macOS a paste that goes ahead while this app still
has the focus types into its own canvas, is counted as a success, and costs the user the
words they just spoke. Better to refuse and leave them on the clipboard.

The second distinction, just as important: "the focus is elsewhere" and "the focus cannot
be seen" both come back as 0, and only the first of them makes pasting safe.
"""

import os

import pytest

from voice_typer import focus as focus_module

TARGET = 4242


class FakeRunningApp:
    """Stands in for NSRunningApplication."""

    def __init__(self, pid: int, on_activate=None):
        self.pid = pid
        self.on_activate = on_activate
        self.activated_from: list[tuple[int, int]] = []

    def processIdentifier(self):
        return self.pid

    def activateFromApplication_options_(self, other, options):
        self.activated_from.append((other.processIdentifier(), options))
        if self.on_activate is not None:
            self.on_activate()
        return True


class FakeAppKit:
    """The three AppKit entry points focus.py uses, and nothing else."""

    def __init__(self, *, front_pid: int, target: FakeRunningApp | None = None):
        self.front_pid = front_pid
        self.target = target
        outer = self

        class _Workspace:
            def frontmostApplication(self):
                return None if outer.front_pid == 0 else FakeRunningApp(outer.front_pid)

        class NSWorkspace:
            @staticmethod
            def sharedWorkspace():
                return _Workspace()

        class NSRunningApplication:
            @staticmethod
            def currentApplication():
                return FakeRunningApp(os.getpid())

            @staticmethod
            def runningApplicationWithProcessIdentifier_(pid):
                return outer.target if outer.target and outer.target.pid == pid else None

        self.NSWorkspace = NSWorkspace
        self.NSRunningApplication = NSRunningApplication

    def target_that_comes_forward(self) -> FakeRunningApp:
        """A target whose activation actually works, as it should on a healthy Mac."""
        self.target = FakeRunningApp(TARGET, on_activate=lambda: setattr(self, "front_pid", TARGET))
        return self.target


@pytest.fixture
def macos(monkeypatch, tmp_path):
    """Run the macOS branch on any machine, with the waits shortened to nothing."""
    monkeypatch.setattr(focus_module, "IS_MACOS", True)
    monkeypatch.setattr(focus_module, "IS_WINDOWS", False)
    monkeypatch.setattr(focus_module, "objc", None)  # no pool needed for a fake
    monkeypatch.setattr(focus_module, "_CRASHED_CALLS", set())
    monkeypatch.setattr(focus_module, "_PROVEN_CALLS", set())
    monkeypatch.setattr(focus_module, "_probe_path", lambda kind: tmp_path / f".probe-{kind}")
    monkeypatch.setattr(focus_module, "MACOS_ACTIVATION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(focus_module, "MACOS_ACTIVATION_POLL_SECONDS", 0.001)
    monkeypatch.setattr(focus_module, "MACOS_FOCUS_SETTLE_SECONDS", 0)
    return tmp_path


def _install(monkeypatch, appkit: FakeAppKit | None) -> None:
    monkeypatch.setattr(focus_module, "AppKit", appkit)


# --------------------------------------------------------------- the healthy macOS paths


def test_the_focus_is_left_alone_when_the_user_is_already_where_they_want_to_be(macos, monkeypatch):
    """Pressing the hotkey never moves the focus, so nothing here should run at all."""
    appkit = FakeAppKit(front_pid=9999, target=FakeRunningApp(TARGET))
    _install(monkeypatch, appkit)

    assert focus_module.return_focus_to(TARGET) is True
    assert appkit.target.activated_from == []


def test_the_previous_application_is_activated_the_cooperative_way(macos, monkeypatch):
    """macOS 14 ignores the old activateIgnoringOtherApps; the focus holder must yield."""
    appkit = FakeAppKit(front_pid=os.getpid())
    target = appkit.target_that_comes_forward()
    _install(monkeypatch, appkit)

    assert focus_module.return_focus_to(TARGET, started_from_our_window=True) is True
    assert target.activated_from == [(os.getpid(), 0)]  # 0, never the deprecated flag


def test_a_refused_activation_stops_the_paste(macos, monkeypatch):
    """The words must not be typed into our own window and then counted as delivered."""
    appkit = FakeAppKit(front_pid=os.getpid(), target=FakeRunningApp(TARGET))
    _install(monkeypatch, appkit)  # activation does nothing: the focus stays here

    assert focus_module.return_focus_to(TARGET, started_from_our_window=True) is False


def test_an_application_that_has_quit_is_not_chased(macos, monkeypatch):
    _install(monkeypatch, FakeAppKit(front_pid=os.getpid()))

    assert focus_module.return_focus_to(TARGET, started_from_our_window=True) is False


def test_holding_the_focus_with_nowhere_to_return_it_refuses(macos, monkeypatch):
    """Our own window is in front and no other application was ever recorded."""
    _install(monkeypatch, FakeAppKit(front_pid=os.getpid()))

    assert focus_module.return_focus_to(0, started_from_our_window=True) is False


def test_our_own_process_is_recognised_on_macos(macos, monkeypatch):
    _install(monkeypatch, FakeAppKit(front_pid=os.getpid()))

    assert focus_module.is_our_window(os.getpid()) is True
    assert focus_module.is_our_window(TARGET) is False
    assert focus_module.is_our_window(0) is False


# ---------------------------------------------------- when macOS cannot be asked at all


def test_an_unreadable_focus_still_lets_the_hotkey_path_paste(macos, monkeypatch):
    """The hotkey never moved the focus, so the words still belong where the caret is.

    This is the common path, and breaking it to guard against a rare failure would cost
    more than it saves."""
    _install(monkeypatch, None)

    assert focus_module.foreground_window() == 0
    assert focus_module.return_focus_to(TARGET) is True


def test_an_unreadable_focus_refuses_the_take_that_started_in_our_window(macos, monkeypatch):
    """Here the focus certainly moved and cannot be checked, so pasting blind would type
    into our own canvas, count as a success, restore the old clipboard over the transcript
    and delete the recording — the words gone from all three places at once."""
    _install(monkeypatch, None)

    assert focus_module.return_focus_to(TARGET, started_from_our_window=True) is False


def test_a_frontmost_read_that_killed_the_last_run_is_treated_as_unreadable(macos, monkeypatch):
    appkit = FakeAppKit(front_pid=os.getpid(), target=FakeRunningApp(TARGET))
    _install(monkeypatch, appkit)
    monkeypatch.setattr(focus_module, "_CRASHED_CALLS", {"frontmost"})

    assert focus_module.foreground_window() == 0
    assert focus_module.return_focus_to(TARGET, started_from_our_window=True) is False
    assert focus_module.return_focus_to(TARGET) is True  # the hotkey path is unaffected


def test_an_activation_that_killed_the_last_run_is_not_tried_again(macos, monkeypatch):
    """One crash, ever — not one per dictation. And the words stay reachable: the focus
    can still be read, so the app knows it is in front and refuses to paste."""
    appkit = FakeAppKit(front_pid=os.getpid(), target=FakeRunningApp(TARGET))
    _install(monkeypatch, appkit)
    monkeypatch.setattr(focus_module, "_CRASHED_CALLS", {"activate"})

    assert focus_module.return_focus_to(TARGET, started_from_our_window=True) is False
    assert appkit.target.activated_from == []  # the call that killed the last run was skipped


# ----------------------------------------------------------------------- the crash probe


def test_the_probe_is_written_during_the_call_and_removed_after(macos, monkeypatch):
    """An AppKit main-thread assertion aborts the process instead of raising, so a file
    left behind is the only evidence the next run would have."""
    probe = macos / ".probe-activate"
    seen: list[bool] = []
    appkit = FakeAppKit(front_pid=os.getpid())
    appkit.target = FakeRunningApp(
        TARGET,
        on_activate=lambda: (seen.append(probe.exists()), setattr(appkit, "front_pid", TARGET)),
    )
    _install(monkeypatch, appkit)

    focus_module.return_focus_to(TARGET, started_from_our_window=True)

    assert seen == [True]  # it existed while the risky call was running
    assert not probe.exists()  # and was cleared the moment it returned


def test_one_call_never_erases_the_crash_record_of_another(macos, monkeypatch):
    """A single shared probe file would let the first frontmost read of the next run
    delete the record of an activation that crashed — and the app would walk into the
    same crash again on every other launch."""
    crashed = macos / ".probe-activate"
    crashed.write_text("", encoding="utf-8")
    _install(monkeypatch, FakeAppKit(front_pid=9999))

    focus_module.foreground_window()  # writes and removes its own probe only

    assert crashed.exists()


def test_the_probe_is_written_once_and_not_on_every_poll(macos, monkeypatch):
    """The watcher reads the front application five times a second; a read that worked on
    a worker thread once is not going to start asserting on the hundredth."""
    _install(monkeypatch, FakeAppKit(front_pid=9999))
    probe = macos / ".probe-frontmost"
    writes: list[int] = []
    original = focus_module.Path.touch

    def counting_touch(self, *args, **kwargs):
        if self == probe:
            writes.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(focus_module.Path, "touch", counting_touch)

    for _ in range(5):
        focus_module.foreground_window()

    assert len(writes) == 1


# ------------------------------------------------------------------------------ Windows


def test_windows_pastes_even_when_the_foreground_change_was_refused(monkeypatch):
    """Unchanged Windows behaviour: somewhere beats nowhere, and it is verified working."""
    monkeypatch.setattr(focus_module, "IS_WINDOWS", True)
    monkeypatch.setattr(focus_module, "IS_MACOS", False)
    monkeypatch.setattr(focus_module, "FOCUS_SETTLE_SECONDS", 0)
    monkeypatch.setattr(focus_module, "foreground_window", lambda: 111)
    monkeypatch.setattr(focus_module, "_windows_window_is_ours", lambda _handle: True)

    def refuse(_handle):
        raise OSError("refused by Windows")

    monkeypatch.setattr(focus_module, "_restore_foreground", refuse)

    assert focus_module.return_focus_to(222, started_from_our_window=True) is True


def test_windows_hands_the_focus_back_to_the_window_the_take_started_in(monkeypatch):
    restored: list[int] = []
    monkeypatch.setattr(focus_module, "IS_WINDOWS", True)
    monkeypatch.setattr(focus_module, "IS_MACOS", False)
    monkeypatch.setattr(focus_module, "FOCUS_SETTLE_SECONDS", 0)
    monkeypatch.setattr(focus_module, "foreground_window", lambda: 111)
    monkeypatch.setattr(focus_module, "_windows_window_is_ours", lambda _handle: True)
    monkeypatch.setattr(focus_module, "_restore_foreground", restored.append)

    assert focus_module.return_focus_to(222) is True
    assert restored == [222]


def test_windows_leaves_the_focus_alone_when_it_is_not_ours(monkeypatch):
    restored: list[int] = []
    monkeypatch.setattr(focus_module, "IS_WINDOWS", True)
    monkeypatch.setattr(focus_module, "IS_MACOS", False)
    monkeypatch.setattr(focus_module, "foreground_window", lambda: 999)
    monkeypatch.setattr(focus_module, "_windows_window_is_ours", lambda _handle: False)
    monkeypatch.setattr(focus_module, "_restore_foreground", restored.append)

    assert focus_module.return_focus_to(222) is True
    assert restored == []
