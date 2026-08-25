"""Where the card was left, and whether that place still exists.

The point of this file is that it needs no desktop. These six behaviours used to live
inside `OverlayWindow`, so exercising them meant building a real Tk window — which is
possible on a developer's machine and impossible on a build server. Every test here runs
anywhere Python does.
"""

import json

import pytest

from voice_typer import window_state
from voice_typer.window_state import SavedWindow

# A 1920x1080 primary screen, and the same with a second monitor to its left.
ONE_SCREEN = (0, 0, 1920, 1080)
TWO_SCREENS = (-1920, 0, 1920, 1080)
CARD_WIDTH = 520
MARGIN = 24
CORNER = (1376, 832)


def choose(position, bounds=ONE_SCREEN):
    return window_state.choose_position(
        position, default=CORNER, width=CARD_WIDTH, margin=MARGIN, bounds=bounds
    )


# --------------------------------------------------------------------------- reading


def test_a_remembered_position_comes_back(tmp_path):
    path = tmp_path / "window.json"
    path.write_text(
        json.dumps({"x": 260, "y": 180, "collapsed": True, "rewrite_mode": True}),
        encoding="utf-8",
    )

    assert window_state.read(path) == SavedWindow((260, 180), collapsed=True, rewrite_mode=True)


def test_a_missing_file_is_a_fresh_start(tmp_path):
    """Not an error: the first run has nothing to remember."""
    assert window_state.read(tmp_path / "never-written.json") == SavedWindow()


def test_a_corrupt_file_is_a_fresh_start(tmp_path):
    path = tmp_path / "window.json"
    path.write_text("not json at all", encoding="utf-8")

    assert window_state.read(path) == SavedWindow()


def test_json_that_is_not_an_object_is_a_fresh_start(tmp_path):
    """`json.loads("[1, 2]")` succeeds, and `.get` on the result would not."""
    path = tmp_path / "window.json"
    path.write_text("[1, 2]", encoding="utf-8")

    assert window_state.read(path) == SavedWindow()


@pytest.mark.parametrize(
    "body",
    [
        {"x": 5},  # half a position is not a position
        {"y": 5},
        {"x": "nowhere", "y": 5},
        {"x": None, "y": 5},
    ],
)
def test_an_incomplete_position_is_dropped_but_the_rest_survives(tmp_path, body):
    path = tmp_path / "window.json"
    path.write_text(json.dumps({**body, "collapsed": True}), encoding="utf-8")

    saved = window_state.read(path)

    assert saved.position is None
    assert saved.collapsed is True


# --------------------------------------------------------------------------- writing


def test_writing_then_reading_returns_the_same_thing(tmp_path):
    path = tmp_path / "nested" / "window.json"
    state = SavedWindow((300, 400), collapsed=True, rewrite_mode=False)

    assert window_state.write(path, state) is True
    assert window_state.read(path) == state


def test_a_write_that_cannot_happen_says_so_instead_of_raising(tmp_path):
    """A directory standing where the file should go. The card must still open — losing
    the remembered corner is a nuisance, a crash on startup is not."""
    path = tmp_path / "window.json"
    path.mkdir()

    assert window_state.write(path, SavedWindow((1, 2))) is False


# -------------------------------------------------------------------------- choosing


def test_a_position_that_is_still_on_screen_is_kept():
    assert choose((260, 180)) == (260, 180)


def test_nothing_remembered_means_the_default_corner():
    assert choose(None) == CORNER


def test_a_position_off_the_edge_of_the_screen_is_ignored():
    """Unplugging a second monitor must not leave the card somewhere unreachable."""
    assert choose((99_000, 99_000)) == CORNER


def test_a_second_monitor_position_is_kept():
    """The whole desktop, not just the primary screen — a card parked on the left-hand
    monitor was always judged off-screen and dragged back."""
    assert choose((-1500, 400), bounds=TWO_SCREENS) == (-1500, 400)


def test_the_same_position_is_refused_once_that_monitor_is_gone():
    """The pair that matters: identical coordinates, different desktop."""
    assert choose((-1500, 400), bounds=ONE_SCREEN) == CORNER


def test_a_card_hanging_off_the_left_edge_keeps_a_grabbable_strip():
    """Enough must remain on screen to grab and drag it back. The cutoff is one margin
    of card still showing."""
    just_enough = -CARD_WIDTH + MARGIN + 1
    assert choose((just_enough, 400)) == (just_enough, 400)
    assert choose((just_enough - 2, 400)) == CORNER


# -------------------------------------------------------------------------- clamping


def test_unfolding_in_the_corner_pulls_the_card_back_on_screen():
    """The card grows in place, and the default corner is the bottom right — so the half
    that appears would otherwise be off the screen."""
    assert window_state.clamp_to_desktop(
        (1800, 1000), width=520, height=176, bounds=ONE_SCREEN
    ) == (1400, 904)


def test_clamping_leaves_a_card_that_already_fits_alone():
    assert window_state.clamp_to_desktop((300, 200), width=520, height=176, bounds=ONE_SCREEN) == (
        300,
        200,
    )


def test_clamping_never_falls_back_to_a_different_corner():
    """Unlike `choose_position`, this keeps the user where they put the card — it only
    pulls in as far as it must."""
    assert window_state.clamp_to_desktop(
        (-3000, 500), width=520, height=176, bounds=TWO_SCREENS
    ) == (-1920, 500)
