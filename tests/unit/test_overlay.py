"""The recorder window — built for real, painted, and driven through every state.

These are the only tests that create an actual Tk window. They exist because the window
is *drawn* rather than laid out: a wrong canvas option or a bad arc angle raises at paint
time, on the Tk thread, where the app swallows it into a log line nobody reads. Painting
every state once catches that in a second.

One window is shared by the whole module on purpose. Tk allows a single root per process,
and creating a second one after the first has been destroyed fails outright — which these
tests found the hard way.

Skipped rather than failed where no desktop session exists.
"""

import json

import pytest

from voice_typer import widget_theme as theme

# Importing the overlay first is deliberate: it points Tcl at the base Python install,
# without which Tk cannot start from inside the virtual environment at all — and this
# whole file would quietly skip, which is worse than failing.
from voice_typer.overlay import APPEARANCE, BAR_COUNT, WINDOW_WIDTH, OverlayWindow


class StubController:
    """Answers everything the window asks, and records what it was told to do."""

    def __init__(self, state="idle"):
        self.state = state
        self.level = 0.5
        self.calls: list[str] = []

    def ui_state(self):
        return self.state

    def ui_elapsed_seconds(self):
        return 65.0

    def ui_level(self):
        return self.level

    def ui_hotkey_label(self):
        return "F9"

    def ui_device_label(self):
        return "მიკროფონი: Microphone Array"

    def usage_text(self):
        return "ხარჯი: $0.0062 · 1.2 წუთი"

    def _record(self, name):
        self.calls.append(name)

    def toggle_recording(self):
        self._record("toggle_recording")

    def toggle_pause(self):
        self._record("toggle_pause")

    def cancel_recording(self):
        self._record("cancel_recording")

    def toggle_enabled(self):
        self._record("toggle_enabled")

    def retry_last(self):
        self._record("retry_last")

    def open_logs(self):
        self._record("open_logs")

    def open_settings(self):
        self._record("open_settings")

    def quit(self):
        self._record("quit")


class FakeEvent:
    def __init__(self, x=0, y=0, x_root=0, y_root=0):
        self.x, self.y = x, y
        self.x_root, self.y_root = x_root, y_root


@pytest.fixture(scope="module")
def shared_window(tmp_path_factory):
    """The one window this process may have.

    There is no separate probe for "can Tk start here": creating a throwaway root and
    destroying it uses up the process's one allowance, and every window after it fails.
    So the first real window is the test.
    """
    controller = StubController()
    path = tmp_path_factory.mktemp("overlay") / "window.json"
    try:
        overlay = OverlayWindow(controller, path)
    except Exception as exc:  # no desktop session, or Tcl unavailable
        pytest.skip(f"no desktop session available: {exc}")

    yield overlay, controller, path
    overlay._destroy()


@pytest.fixture
def window(shared_window):
    """Hand each test a clean slate on the one window that exists."""
    overlay, controller, path = shared_window
    controller.state = "idle"
    controller.level = 0.5
    controller.calls.clear()
    overlay._levels = [0.0] * BAR_COUNT
    overlay._update()
    controller.calls.clear()
    return overlay, controller, path


def centre_of(overlay, name):
    x0, y0, x1, y1 = overlay._buttons[name].box
    return FakeEvent(x=(x0 + x1) // 2, y=(y0 + y1) // 2)


def click(overlay, name):
    event = centre_of(overlay, name)
    overlay._on_press(event)
    overlay._on_release(event)


def test_the_window_builds_and_paints(window):
    overlay, _, _ = window
    assert overlay._root.winfo_exists()
    assert len(overlay._bars) == BAR_COUNT


@pytest.mark.parametrize("state", sorted(APPEARANCE))
def test_every_state_paints_without_raising(window, state):
    """A bad canvas option only shows up when that state is actually drawn."""
    overlay, controller, _ = window
    controller.state = state

    overlay._update()
    overlay._update()  # twice, so the enable/disable transitions run too

    _colour, words = APPEARANCE[state]
    assert overlay._canvas.itemcget(overlay._status_text, "text") == words


def test_the_timer_is_shown_as_minutes_and_seconds(window):
    overlay, _, _ = window
    assert overlay._canvas.itemcget(overlay._timer_text, "text") == "1:05"


def test_the_meter_scrolls_the_level_history(window):
    overlay, controller, _ = window
    controller.state = "recording"
    controller.level = 0.9

    before = list(overlay._levels)
    overlay._update()

    assert overlay._levels[-1] == 0.9
    assert overlay._levels[:-1] == before[1:]


def test_the_meter_falls_silent_when_not_recording(window):
    overlay, controller, _ = window
    controller.state = "recording"
    controller.level = 0.9
    overlay._update()

    controller.state = "idle"
    for _ in range(BAR_COUNT):
        overlay._update()

    assert max(overlay._levels) == 0.0


def test_clicking_a_button_runs_its_command(window):
    overlay, controller, _ = window
    click(overlay, "record")
    assert controller.calls == ["toggle_recording"]


def test_the_power_button_closes_the_app(window):
    """It reads as 'turn this off' to everyone, so it must not merely mute the hotkey."""
    overlay, controller, _ = window
    click(overlay, "power")
    assert controller.calls == ["quit"]


def test_pause_and_cancel_work_while_recording(window):
    overlay, controller, _ = window
    controller.state = "recording"
    overlay._update()

    click(overlay, "pause")
    click(overlay, "cancel")

    assert controller.calls == ["toggle_pause", "cancel_recording"]


def test_a_disabled_button_does_nothing(window):
    overlay, controller, _ = window  # idle, so pause is disabled
    click(overlay, "pause")
    assert controller.calls == []


def test_pressing_the_card_and_releasing_over_a_button_does_not_fire_it(window):
    """Dragging the window from one place to another must not trip a button on the way."""
    overlay, controller, _ = window

    overlay._on_press(FakeEvent(x=10, y=10, x_root=500, y_root=500))
    overlay._on_release(centre_of(overlay, "record"))

    assert controller.calls == []


def test_dragging_moves_the_window_and_remembers_where(window):
    overlay, _, path = window
    overlay._root.geometry("+300+300")
    overlay._root.update_idletasks()

    overlay._on_press(FakeEvent(x=8, y=8, x_root=308, y_root=308))
    overlay._on_drag(FakeEvent(x_root=408, y_root=358))
    overlay._root.update_idletasks()
    overlay._on_release(FakeEvent(x=8, y=8))

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert (saved["x"], saved["y"]) == (400, 350)


def test_a_remembered_position_is_used_again(window):
    overlay, _, path = window
    path.write_text(json.dumps({"x": 260, "y": 180}), encoding="utf-8")
    assert overlay._restore_position() == (260, 180)


def test_a_position_off_the_edge_of_the_screen_is_ignored(window):
    """Unplugging a second monitor must not leave the window somewhere unreachable."""
    overlay, _, path = window
    path.write_text(json.dumps({"x": 99_000, "y": 99_000}), encoding="utf-8")

    x, _y = overlay._restore_position()

    assert x < overlay._root.winfo_screenwidth() - WINDOW_WIDTH + 1


def test_a_corrupt_position_file_is_ignored(window):
    overlay, _, path = window
    path.write_text("not json at all", encoding="utf-8")
    assert overlay._restore_position()  # falls back to the default corner


def test_a_button_that_raises_does_not_take_the_window_down(window):
    overlay, _, _ = window
    original = overlay._buttons["record"].command

    def explode():
        raise RuntimeError("something went wrong in the app")

    overlay._buttons["record"].command = explode
    try:
        click(overlay, "record")  # must not raise
    finally:
        overlay._buttons["record"].command = original

    assert overlay._root.winfo_exists()


def test_hovering_lights_the_button_and_leaving_puts_it_back(window):
    overlay, _, _ = window
    first_row = overlay._buttons["record"].fill_items[0]

    resting = overlay._canvas.itemcget(first_row, "fill")
    overlay._set_hover("record")
    hovered = overlay._canvas.itemcget(first_row, "fill")
    overlay._set_hover(None)

    assert hovered != resting
    assert overlay._canvas.itemcget(first_row, "fill") == resting


def test_the_menu_shows_the_running_cost_and_whether_the_hotkey_is_live(window):
    overlay, controller, _ = window
    overlay._show_menu(FakeEvent(x_root=-4000, y_root=-4000))  # off-screen, then dismissed
    overlay._menu.unpost()

    assert overlay._menu.entrycget(0, "label") == controller.usage_text()
    assert overlay._menu.entrycget(2, "label").startswith("✓")


def test_closing_is_safe_to_ask_for_from_another_thread(window):
    overlay, _, _ = window
    overlay.close()
    try:
        assert overlay._closing
        assert overlay._root.winfo_exists()  # the flag alone destroys nothing
    finally:
        overlay._closing = False


def test_colours_blend_and_mix_within_range():
    assert theme.blend("#ffffff", "#000000", 0.5) == "#808080"
    assert theme.mix("#000000", "#ffffff", 0.0) == "#000000"
    assert theme.mix("#000000", "#ffffff", 1.0) == "#ffffff"
    assert theme.to_hex((300, -20, 128)) == "#ff0080"
