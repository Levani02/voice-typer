"""The timing rules, driven by injected timestamps instead of a real keyboard."""

from voice_typer.hotkey import Action, HotkeyLogic

THRESHOLD_MS = 400


def make_logic() -> HotkeyLogic:
    return HotkeyLogic(hold_threshold_ms=THRESHOLD_MS)


def test_press_starts_recording():
    logic = make_logic()
    assert logic.on_press(0) is Action.START
    assert logic.is_recording


def test_long_hold_stops_on_release():
    logic = make_logic()
    logic.on_press(0)
    assert logic.on_release(THRESHOLD_MS) is Action.STOP
    assert not logic.is_recording


def test_hold_exactly_at_threshold_counts_as_a_hold():
    """The boundary belongs to push-to-talk — one millisecond either way must be decisive."""
    logic = make_logic()
    logic.on_press(1000)
    assert logic.on_release(1000 + THRESHOLD_MS) is Action.STOP


def test_hold_one_millisecond_short_becomes_a_toggle():
    logic = make_logic()
    logic.on_press(1000)
    assert logic.on_release(1000 + THRESHOLD_MS - 1) is Action.NONE
    assert logic.is_recording  # still latched on


def test_second_tap_stops_the_toggle():
    logic = make_logic()
    logic.on_press(0)
    logic.on_release(100)  # short tap — latched
    assert logic.on_press(2000) is Action.STOP
    assert not logic.is_recording


def test_release_after_the_second_tap_does_nothing():
    logic = make_logic()
    logic.on_press(0)
    logic.on_release(100)
    logic.on_press(2000)
    assert logic.on_release(2100) is Action.NONE
    assert not logic.is_recording


def test_escape_cancels_while_holding():
    logic = make_logic()
    logic.on_press(0)
    assert logic.on_escape() is Action.CANCEL
    assert not logic.is_recording


def test_release_after_escape_does_not_stop_again():
    """The hotkey is still physically down after Escape — its release must be swallowed."""
    logic = make_logic()
    logic.on_press(0)
    logic.on_escape()
    assert logic.on_release(5000) is Action.NONE


def test_escape_cancels_a_latched_recording():
    logic = make_logic()
    logic.on_press(0)
    logic.on_release(100)
    assert logic.on_escape() is Action.CANCEL
    assert not logic.is_recording


def test_escape_while_idle_does_nothing():
    logic = make_logic()
    assert logic.on_escape() is Action.NONE


def test_repeated_press_while_holding_does_not_restart():
    """Windows repeats key-down events while a key is held; they must be ignored."""
    logic = make_logic()
    logic.on_press(0)
    assert logic.on_press(50) is Action.NONE
    assert logic.on_press(100) is Action.NONE
    assert logic.on_release(THRESHOLD_MS + 100) is Action.STOP


def test_force_idle_after_auto_stop_swallows_the_pending_release():
    logic = make_logic()
    logic.on_press(0)
    logic.force_idle()  # the app hit its recording ceiling and stopped by itself
    assert not logic.is_recording
    assert logic.on_release(9000) is Action.NONE


def test_a_full_cycle_can_be_repeated():
    logic = make_logic()
    for start in (0, 10_000, 20_000):
        assert logic.on_press(start) is Action.START
        assert logic.on_release(start + THRESHOLD_MS + 1) is Action.STOP
