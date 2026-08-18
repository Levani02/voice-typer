"""Window behaviour Tk cannot express, per system.

Every function here has to fail softly: a window that is slightly wrong is worth having,
and a window that takes the app down with it is not.
"""

import tkinter as tk

import pytest

from voice_typer import window_platform


class FakeRoot:
    """Just enough of a Tk window to see which attribute was asked for."""

    def __init__(self, refuse: bool = False) -> None:
        self.refuse = refuse
        self.requested: list[tuple] = []

    def attributes(self, *args):
        self.requested.append(args)
        if self.refuse:
            raise tk.TclError("this platform does not support that attribute")

    def tk_call_stub(self, *args):
        self.requested.append(args)


@pytest.fixture
def pretend(monkeypatch):
    def choose(system: str) -> None:
        monkeypatch.setattr(window_platform, "IS_WINDOWS", system == "windows")
        monkeypatch.setattr(window_platform, "IS_MACOS", system == "macos")

    return choose


def test_a_mac_is_never_scaled_twice(pretend):
    """Retina is handled below the drawing API — multiplying again would double the window."""
    pretend("macos")
    assert window_platform.display_scale() == 1.0


def test_the_windows_scale_is_at_least_one(pretend):
    pretend("windows")
    assert window_platform.display_scale() >= 1.0


def test_the_desktop_extent_is_admitted_to_be_unknown_off_windows(pretend):
    """None means "ask Tk for the primary screen instead", not "no monitors"."""
    pretend("macos")
    assert window_platform.desktop_bounds() is None


def test_windows_transparency_nominates_the_key_colour(pretend):
    pretend("windows")
    root = FakeRoot()

    painted = window_platform.apply_transparency(root, "#010203", "#111111")

    assert painted == "#010203"
    assert root.requested == [("-transparentcolor", "#010203")]


def test_macos_transparency_uses_the_system_colour(pretend):
    pretend("macos")
    root = FakeRoot()

    painted = window_platform.apply_transparency(root, "#010203", "#111111")

    assert painted == "systemTransparent"
    assert root.requested == [("-transparent", True)]


def test_a_refused_transparency_falls_back_to_an_opaque_card(pretend):
    """The corners stop being round. That is a blemish, not a failure."""
    pretend("windows")
    root = FakeRoot(refuse=True)

    assert window_platform.apply_transparency(root, "#010203", "#111111") == "#111111"


def test_an_unknown_system_gets_an_opaque_card(pretend):
    pretend("linux")
    root = FakeRoot()

    assert window_platform.apply_transparency(root, "#010203", "#111111") == "#111111"
    assert root.requested == []


def test_a_window_that_cannot_be_made_non_activating_is_left_as_it_is(pretend):
    pretend("macos")

    class Hopeless:
        @property
        def tk(self):
            raise RuntimeError("no interpreter here")

    window_platform.make_non_activating(Hopeless())  # must not raise


def test_nothing_is_attempted_on_an_unknown_system(pretend):
    pretend("linux")

    class Watched:
        touched = False

        @property
        def tk(self):
            Watched.touched = True
            raise AssertionError("should never be reached")

    window_platform.make_non_activating(Watched())
    assert not Watched.touched
