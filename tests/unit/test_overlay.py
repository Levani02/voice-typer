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
from voice_typer.overlay import APPEARANCE, BAR_COUNT, WINDOW_WIDTH, OverlayWindow, tk


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
    except tk.TclError as exc:
        # Only a missing display is a reason to skip. A wider `except Exception` here
        # turned every real crash in the constructor — which paints the entire card —
        # into "no desktop session", and the suite went green on a broken window.
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
    if overlay._collapsed:
        # One window serves the whole module, so a test that folded it must not hand the
        # next one a card with no buttons on it.
        overlay._collapsed = False
        overlay._rebuild()
        overlay._root.update_idletasks()  # or the next test measures the folded size
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

    assert overlay._canvas.itemcget(overlay._status_text, "text") == APPEARANCE[state].words


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
    """`_sync_menu`, never `_show_menu`: posting the menu enters Windows' own modal loop,
    which cannot return without a human to dismiss it — it hangs the whole suite."""
    overlay, controller, _ = window
    overlay._sync_menu()

    assert overlay._menu.entrycget(0, "label") == controller.usage_text()
    assert overlay._menu.entrycget(2, "label").startswith("✓")


def test_the_menu_says_when_the_hotkey_is_switched_off(window):
    overlay, controller, _ = window
    controller.state = "disabled"
    overlay._sync_menu()

    assert not overlay._menu.entrycget(2, "label").startswith("✓")


def test_closing_is_safe_to_ask_for_from_another_thread(window):
    overlay, _, _ = window
    overlay.close()
    try:
        assert overlay._closing
        assert overlay._root.winfo_exists()  # the flag alone destroys nothing
    finally:
        overlay._closing = False


def test_the_waveform_stays_calm_while_the_dot_goes_red(window):
    """One colour for every surface turned the whole card into a warning light."""
    recording = APPEARANCE["recording"]
    assert recording.dot != theme.ACCENT
    assert recording.wave == theme.ACCENT
    assert recording.timer != recording.dot


def test_the_microphone_greys_out_with_its_own_label(window):
    """A bright cyan mic beside a greyed-out label reads as a live button."""
    overlay, controller, _ = window
    controller.state = "transcribing"
    overlay._update()

    body = overlay._record_icon[0]
    assert overlay._canvas.itemcget(body, "outline") == theme.DISABLED_INK
    assert overlay._canvas.itemcget(overlay._record_label, "fill") == theme.DISABLED_INK


def test_a_button_disabled_under_the_pointer_stops_looking_clickable(window):
    """Otherwise it keeps its lit gradient, and the next click there drags the window."""
    overlay, controller, _ = window
    controller.state = "recording"
    overlay._update()
    overlay._set_hover("cancel")
    lit = overlay._canvas.itemcget(overlay._buttons["cancel"].fill_items[0], "fill")

    controller.state = "idle"
    overlay._update()

    assert not overlay._buttons["cancel"].hovered
    assert overlay._canvas.itemcget(overlay._buttons["cancel"].fill_items[0], "fill") != lit


def test_the_button_contents_stay_centred_when_the_label_changes(window):
    """The group used to jump sideways by 8 pixels between "ჩაწერა" and "გაჩერება"."""
    overlay, controller, _ = window
    box = overlay._buttons["record"].box

    def offset():
        bounds = overlay._canvas.bbox(*overlay._record_icon, overlay._record_label)
        return abs((bounds[0] + bounds[2]) / 2 - (box[0] + box[2]) / 2)

    idle_offset = offset()
    controller.state = "recording"
    overlay._update()

    assert idle_offset <= 2
    assert offset() <= 2


def test_a_plain_click_does_not_rewrite_the_saved_position(window):
    overlay, _, path = window
    overlay._on_press(FakeEvent(x=10, y=10, x_root=500, y_root=500))
    overlay._on_release(FakeEvent(x=10, y=10))
    before = path.read_text(encoding="utf-8") if path.exists() else None

    for _ in range(3):
        overlay._on_press(FakeEvent(x=10, y=10, x_root=500, y_root=500))
        overlay._on_release(FakeEvent(x=10, y=10))

    after = path.read_text(encoding="utf-8") if path.exists() else None
    assert before == after


def test_a_second_monitor_position_is_kept(window, monkeypatch):
    """winfo_screenwidth only measures the primary display, so a window parked on another
    screen was always dragged back."""
    overlay, _, path = window
    monkeypatch.setattr(overlay, "_desktop_bounds", lambda: (-1920, 0, 1920, 1080))
    path.write_text(json.dumps({"x": -1500, "y": 400}), encoding="utf-8")

    assert overlay._restore_position() == (-1500, 400)


def test_the_status_dot_keeps_a_graded_halo(window):
    """Recolouring the rings with one alpha gives the glow a hard edge."""
    overlay, _, _ = window
    overlay._update()

    rings = [overlay._canvas.itemcget(item, "fill") for item in overlay._dot_items]
    assert len(set(rings)) == len(rings)  # every ring a different shade


def test_colours_blend_and_mix_within_range():
    assert theme.blend("#ffffff", "#000000", 0.5) == "#808080"
    assert theme.mix("#000000", "#ffffff", 0.0) == "#000000"
    assert theme.mix("#000000", "#ffffff", 1.0) == "#ffffff"
    assert theme.to_hex((300, -20, 128)) == "#ff0080"


# --------------------------------------------------------------------------- folding


def test_folding_the_card_away_leaves_only_the_light_and_the_clock(window):
    overlay, _, _ = window
    full_width = overlay._root.winfo_width()

    click(overlay, "fold")
    overlay._root.update_idletasks()

    assert overlay._collapsed
    assert overlay._root.winfo_width() < full_width
    assert set(overlay._buttons) == {"fold"}  # nothing left that needs looking at


def test_unfolding_brings_every_control_back(window):
    overlay, _, _ = window
    full_width = overlay._root.winfo_width()

    click(overlay, "fold")
    click(overlay, "fold")
    overlay._root.update_idletasks()

    assert not overlay._collapsed
    assert overlay._root.winfo_width() == full_width
    assert "record" in overlay._buttons


@pytest.mark.parametrize("state", sorted(APPEARANCE))
def test_every_state_paints_while_folded(window, state):
    """The folded card carries none of the items `_update` normally writes to. Reaching
    for one of them would raise fourteen times a second, into a log nobody reads."""
    overlay, controller, _ = window
    click(overlay, "fold")

    controller.state = state
    overlay._update()

    assert overlay._collapsed


def test_the_folded_choice_is_remembered(window):
    overlay, _, path = window

    click(overlay, "fold")

    assert json.loads(path.read_text(encoding="utf-8"))["collapsed"] is True


def test_unfolding_in_the_corner_keeps_the_card_on_the_screen(window):
    """The window opens in the bottom-right corner, which is the one place where growing
    it back to full width would push most of it off the edge."""
    overlay, _, _ = window
    click(overlay, "fold")
    overlay._root.update_idletasks()
    overlay._root.geometry(f"+{overlay._root.winfo_screenwidth() - 40}+100")
    overlay._root.update_idletasks()

    click(overlay, "fold")
    overlay._root.update_idletasks()

    right_edge = overlay._root.winfo_x() + overlay._root.winfo_width()
    assert right_edge <= overlay._root.winfo_screenwidth()
