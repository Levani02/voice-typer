"""The card's measurements — checked without drawing anything.

These sums used to live inside `OverlayWindow`, so getting at them meant building a real
Tk window. They decide how big the card is and how big its lettering is, which is exactly
the sort of thing that should be pinned down somewhere a build server can run.
"""

import pytest

from voice_typer.card_layout import (
    COLLAPSED_HEIGHT,
    COLLAPSED_MODE_EXTRA,
    COLLAPSED_WIDTH,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
    Button,
    Metrics,
    format_elapsed,
)

PLAIN = Metrics(1.0, 1.0)
DOUBLED = Metrics(2.0, 2.0)
# A half-size card whose lettering was left at full size — the case the second factor exists for.
SMALL_CARD_BIG_TEXT = Metrics(0.5, 1.0)


# --------------------------------------------------------------------------- scaling


def test_at_100_percent_a_design_pixel_is_a_screen_pixel():
    assert PLAIN.s(22) == 22
    assert PLAIN.c(22) == 22


def test_on_a_doubled_display_every_measurement_doubles():
    assert DOUBLED.s(22) == 44
    assert DOUBLED.c(22) == 44


def test_text_and_layout_scale_independently():
    """The whole reason there are two factors: shrinking the card must be allowed to
    leave the writing readable."""
    assert SMALL_CARD_BIG_TEXT.s(100) == 50
    assert SMALL_CARD_BIG_TEXT.c(100) == 100


def test_a_font_is_measured_in_pixels_not_points():
    """Negative means pixels to Tk. A positive number here would size in points and the
    card would come out wrong on every scaled display."""
    family, size, weight = PLAIN.font("Segoe UI", 13, "bold")
    assert (family, weight) == ("Segoe UI", "bold")
    assert size == -13


def test_a_font_never_shrinks_to_nothing():
    """Rounding a small design size at a small scale reaches zero, and Tk rejects it."""
    _family, size, _weight = Metrics(0.05, 0.05).font("Segoe UI", 1)
    assert size == -1


# ------------------------------------------------------------------------ card size


@pytest.mark.parametrize("rewrite", [False, True])
def test_the_open_card_is_one_width_in_both_modes(rewrite):
    """Open, there is room for the mode to be spelled out inside the existing card."""
    assert PLAIN.card_width(folded=False, rewrite=rewrite) == WINDOW_WIDTH
    assert PLAIN.card_height(folded=False) == WINDOW_HEIGHT


def test_the_folded_strip_is_narrow_in_the_ordinary_mode():
    assert PLAIN.card_width(folded=True, rewrite=False) == COLLAPSED_WIDTH
    assert PLAIN.card_height(folded=True) == COLLAPSED_HEIGHT


def test_the_folded_strip_grows_to_keep_the_rewrite_mode_visible():
    """Rewrite mode changes what lands at the cursor, so folding must never hide it."""
    folded_rewrite = PLAIN.card_width(folded=True, rewrite=True)
    assert folded_rewrite == COLLAPSED_WIDTH + COLLAPSED_MODE_EXTRA
    assert folded_rewrite > PLAIN.card_width(folded=True, rewrite=False)


def test_the_content_edges_sit_inside_the_card():
    left, right = PLAIN.inner_edges()
    assert 0 < left < right < WINDOW_WIDTH


def test_a_bleed_widens_the_content_edges_symmetrically():
    left, right = PLAIN.inner_edges()
    bled_left, bled_right = PLAIN.inner_edges(bleed=10)
    assert left - bled_left == bled_right - right == 10


# --------------------------------------------------------------------------- the clock


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [(0, "0:00"), (9, "0:09"), (65, "1:05"), (600, "10:00"), (3599, "59:59")],
)
def test_the_timer_reads_as_minutes_and_seconds(seconds, shown):
    assert format_elapsed(seconds) == shown


def test_a_negative_elapsed_time_shows_zero_rather_than_a_minus_sign():
    """The clock is read at a glance while speaking; it must never show something odd."""
    assert format_elapsed(-3.0) == "0:00"


def test_part_seconds_are_not_rounded_up():
    """0.9 seconds is still the zeroth second — rounding up would show 0:01 before the
    first second has passed."""
    assert format_elapsed(0.9) == "0:00"


# ---------------------------------------------------------------------------- buttons


def test_a_button_knows_whether_a_click_landed_on_it():
    button = Button((10, 20, 110, 60), lambda: None, "a", "b", "c", "d")

    assert button.contains(60, 40)
    assert button.contains(10, 20)  # the edges count
    assert button.contains(110, 60)


def test_a_click_outside_a_button_is_not_the_button():
    button = Button((10, 20, 110, 60), lambda: None, "a", "b", "c", "d")

    assert not button.contains(9, 40)
    assert not button.contains(60, 61)
