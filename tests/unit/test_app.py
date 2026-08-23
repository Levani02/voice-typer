"""The state machine — the part most likely to lose the user's words.

Every collaborator is a stand-in, so these run with no microphone, no keyboard hook and
no network. What they protect: every take's audio is on disk before it is uploaded and
gone only once its own text has landed, the tray describes what is happening rather than
what a thread just finished, and no two jobs ever paste over each other.
"""

import os
import threading
import time

import pytest

from voice_typer import app as app_module
from voice_typer.app import App
from voice_typer.config import Config
from voice_typer.hotkey import Action
from voice_typer.injector import ClipboardUnavailableError, PasteFailedError
from voice_typer.recorder import Recording, build_wav
from voice_typer.transcriber import (
    NothingToPasteError,
    Transcript,
    TranscriptionError,
    Usage,
)
from voice_typer.tray import TrayState

TRANSCRIPT = "გამარჯობა"
SAMPLE_RATE = 16_000


def make_config(**overrides) -> Config:
    values = {
        "api_key": "test-key-not-real",
        "hotkey": "f9",
        "hold_threshold_ms": 400,
        "max_recording_seconds": 300,
        "min_recording_ms": 300,
        "input_device": None,
        "sample_rate": SAMPLE_RATE,
        "restore_clipboard": True,
        "clipboard_restore_delay_ms": 0,
        "language_code": "kat",
        "model_id": "scribe_v2",
        "no_verbatim": False,
        "price_per_hour_usd": 0.22,
        "keyterms": (),
        # Off by default here so the app tests read the transcriber's own output. The
        # filtering itself is tested where it lives, in `test_fillers.py`.
        "filler_words": (),
        "summary_instruction": "შეაჯამე:",
        "prune_takes_after_days": 7,
        "window_scale": 1.0,
        "content_scale": 1.0,
        "log_transcripts": False,
    }
    return Config(**{**values, **overrides})


def audio(seconds: float = 2.0, marker: bytes = b"\x00\x00") -> Recording:
    """A recording whose bytes are recognisable, so takes can be told apart on disk."""
    pcm = marker * int(SAMPLE_RATE * seconds)
    return Recording(wav_bytes=build_wav(pcm, SAMPLE_RATE), duration_seconds=seconds)


class FakeRecorder:
    def __init__(self):
        self.is_recording = False
        self.is_paused = False
        self.cancelled = False
        self.level = 0.0
        self.elapsed_seconds = 0.0

    def start(self):
        self.is_recording = True
        self.is_paused = False

    def stop(self):
        self.is_recording = False
        self.is_paused = False
        return audio()

    def cancel(self):
        self.is_recording = False
        self.is_paused = False
        self.cancelled = True

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False


class FakeTranscriber:
    """Returns a transcript, raises, or blocks until released — one per test."""

    def __init__(self, result=TRANSCRIPT, error=None, gate=None):
        self.result = result
        self.error = error
        self.gate = gate
        self.calls = 0
        self.started = threading.Event()

    def transcribe(self, wav_bytes):
        self.calls += 1
        self.started.set()
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if self.error is not None:
            raise self.error
        if isinstance(self.result, Transcript):
            return self.result
        return Transcript(text=self.result)


class FakeUsage:
    """Records what it was charged, so tests can assert the tally actually happened."""

    def __init__(self):
        self.charged: list[float] = []

    def add(self, seconds):
        self.charged.append(seconds)
        return Usage(calls=len(self.charged), total_seconds=sum(self.charged), total_cost_usd=0.0)

    def read(self):
        return Usage(calls=len(self.charged), total_seconds=sum(self.charged), total_cost_usd=0.0)


class FakeHotkeyLogic:
    def force_idle(self):
        pass

    def force_recording(self):
        pass


class FakeHotkey:
    def __init__(self):
        self.logic = FakeHotkeyLogic()
        self.stopped = False
        self.starts = 0

    def start(self):
        self.starts += 1

    def stop(self):
        self.stopped = True


class FakeTray:
    def __init__(self):
        self.states: list[TrayState] = []
        self.messages: list[str] = []
        self.menu_refreshes = 0

    def set_state(self, state):
        self.states.append(state)

    def notify(self, message, title=None):
        self.messages.append(message)

    def refresh_menu(self):
        self.menu_refreshes += 1


@pytest.fixture
def logs(tmp_path, monkeypatch):
    """Point every on-disk path at a temporary folder."""
    monkeypatch.setattr(app_module, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(app_module, "PENDING_DIR", tmp_path / "pending")
    monkeypatch.setattr(app_module, "LAST_TRANSCRIPT_PATH", tmp_path / "last_transcript.txt")
    return tmp_path


@pytest.fixture
def pasted(monkeypatch):
    """Capture what would have been pasted, instead of touching the real clipboard."""
    captured: list[str] = []
    monkeypatch.setattr(app_module, "inject_text", lambda text, **kwargs: captured.append(text))
    return captured


def build_app(config=None, transcriber=None, tray=None, recorder=None, usage=None):
    app = App(
        config or make_config(),
        recorder=recorder or FakeRecorder(),
        transcriber=transcriber or FakeTranscriber(),
        usage=usage or FakeUsage(),
        hotkey=FakeHotkey(),
    )
    app.attach_tray(tray or FakeTray())
    return app


def kept_recordings() -> list:
    return sorted(app_module.PENDING_DIR.glob(app_module.PENDING_PATTERN))


def wait_for_recordings(count: int, timeout: float = 5.0) -> list:
    """The audio is written on the worker thread, so tests have to wait for it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = kept_recordings()
        if len(found) >= count:
            return found
        time.sleep(0.01)
    raise AssertionError(f"expected {count} kept recordings, found {len(kept_recordings())}")


def run_one_job(app, recording=None):
    """Dictate once, synchronously — the worker is awaited before returning."""
    app._spawn_job(recording or audio())
    assert app._wait_for_jobs(5), "the worker did not finish"


def _raiser(exc):
    def fail(_text, **_kwargs):
        raise exc

    return fail


# --------------------------------------------------------------- the happy path


def test_a_successful_dictation_pastes_the_text(logs, pasted):
    app = build_app()
    run_one_job(app)
    assert pasted == [TRANSCRIPT]


def test_the_recording_is_deleted_once_the_text_has_landed(logs, pasted):
    app = build_app()
    run_one_job(app)
    assert kept_recordings() == []


def test_the_icon_ends_up_idle(logs, pasted):
    tray = FakeTray()
    app = build_app(tray=tray)
    run_one_job(app)
    assert tray.states[-1] is TrayState.IDLE


# ------------------------------------------------------- nothing is ever lost


def test_the_recording_is_on_disk_before_the_upload_starts(logs, pasted):
    """A crash, a power cut, or Quit mid-upload must still leave something to re-send."""
    gate = threading.Event()
    transcriber = FakeTranscriber(gate=gate)
    app = build_app(transcriber=transcriber)

    app._spawn_job(audio())
    assert transcriber.started.wait(timeout=5)
    assert len(kept_recordings()) == 1  # saved before the upload, which is still running

    gate.set()
    assert app._wait_for_jobs(5)


def test_a_failed_transcription_keeps_the_recording(logs, pasted):
    app = build_app(transcriber=FakeTranscriber(error=TranscriptionError("no network")))
    run_one_job(app)

    assert len(kept_recordings()) == 1
    assert pasted == []


def test_a_refused_paste_keeps_the_recording(logs, monkeypatch):
    """The text is on the clipboard, but until it is in a window the audio stays."""
    monkeypatch.setattr(app_module, "inject_text", _raiser(PasteFailedError("window refused")))
    app = build_app()
    run_one_job(app)
    assert len(kept_recordings()) == 1


def test_an_unusable_clipboard_writes_the_text_to_a_file(logs, monkeypatch):
    """Ctrl+V would find nothing here, so the words have to go somewhere reachable."""
    monkeypatch.setattr(
        app_module, "inject_text", _raiser(ClipboardUnavailableError("clipboard locked"))
    )
    app = build_app()
    run_one_job(app)

    assert app_module.LAST_TRANSCRIPT_PATH.read_text(encoding="utf-8") == TRANSCRIPT


def test_the_two_paste_failures_give_opposite_advice(logs, monkeypatch):
    """Naming the paste shortcut when the clipboard write failed sends the user nowhere.

    The shortcut is read from the app rather than written out here: it is Ctrl+V on
    Windows and Cmd+V on macOS, and hard-coding either one tests the wrong system.
    """
    shortcut = app_module.PASTE_SHORTCUT_LABEL

    tray = FakeTray()
    monkeypatch.setattr(app_module, "inject_text", _raiser(PasteFailedError("refused")))
    run_one_job(build_app(tray=tray))
    assert shortcut in tray.messages[-1]

    tray = FakeTray()
    monkeypatch.setattr(app_module, "inject_text", _raiser(ClipboardUnavailableError("locked")))
    run_one_job(build_app(tray=tray))
    assert shortcut not in tray.messages[-1]


# --------------------------------------------- overlapping takes keep their own audio


def test_a_second_take_does_not_overwrite_the_first_take_s_audio(logs, pasted):
    """Each take gets its own file — one shared file destroyed the earlier safety net."""
    gate = threading.Event()
    app = build_app(transcriber=FakeTranscriber(gate=gate))

    app._spawn_job(audio(marker=b"\x11\x11"))
    assert app._transcriber.started.wait(timeout=5)
    app._spawn_job(audio(marker=b"\x22\x22"))  # queued while the first is uploading

    files = wait_for_recordings(2)
    assert files[0].read_bytes() != files[1].read_bytes()

    gate.set()
    assert app._wait_for_jobs(5)


def test_a_succeeding_take_does_not_delete_another_take_s_audio(logs, monkeypatch):
    """Take A succeeding used to delete the file that by then belonged to take B."""
    outcomes = {"paste_fails": False}

    def paste(_text, **_kwargs):
        if outcomes["paste_fails"]:
            raise PasteFailedError("the second window refused it")

    monkeypatch.setattr(app_module, "inject_text", paste)
    app = build_app()

    run_one_job(app, audio(marker=b"\x11\x11"))  # take A succeeds
    outcomes["paste_fails"] = True
    run_one_job(app, audio(marker=b"\x22\x22"))  # take B fails

    assert len(kept_recordings()) == 1  # B survived; A cleaned up after itself


def test_retry_never_re_sends_a_take_that_is_still_running(logs, pasted):
    """Two clicks on Retry used to paste the same words twice."""
    gate = threading.Event()
    transcriber = FakeTranscriber(gate=gate)
    app = build_app(transcriber=transcriber)

    app._spawn_job(audio())
    assert transcriber.started.wait(timeout=5)

    app.retry_last()  # the file on disk belongs to the running job

    gate.set()
    assert app._wait_for_jobs(5)
    assert transcriber.calls == 1
    assert pasted == [TRANSCRIPT]


def test_a_queued_job_puts_the_icon_back_to_transcribing(logs, pasted):
    """A job waiting behind another must not run while the icon says 'ready'."""
    gate = threading.Event()
    transcriber = FakeTranscriber(gate=gate)
    tray = FakeTray()
    app = build_app(transcriber=transcriber, tray=tray)

    app._spawn_job(audio())
    assert transcriber.started.wait(timeout=5)
    app._spawn_job(audio())

    gate.set()
    assert app._wait_for_jobs(5)

    assert transcriber.calls == 2
    assert tray.states[-1] is TrayState.IDLE
    assert tray.states.count(TrayState.TRANSCRIBING) >= 3


def test_retry_says_so_when_there_is_nothing_to_retry(logs):
    tray = FakeTray()
    app = build_app(tray=tray)
    app.retry_last()
    assert tray.messages


def test_retry_resends_a_kept_recording(logs, pasted):
    app = build_app(transcriber=FakeTranscriber(error=TranscriptionError("offline")))
    run_one_job(app)
    assert len(kept_recordings()) == 1

    app._transcriber.error = None  # the network came back
    app.retry_last()
    assert app._wait_for_jobs(5)

    assert pasted == [TRANSCRIPT]
    assert kept_recordings() == []


def test_numbering_continues_past_what_an_earlier_session_left(logs, pasted):
    """Otherwise a new session would overwrite the recording it was meant to preserve."""
    app_module.PENDING_DIR.mkdir(parents=True)
    (app_module.PENDING_DIR / "take-0007.wav").write_bytes(b"from a previous run")

    app = build_app(transcriber=FakeTranscriber(error=TranscriptionError("offline")))
    app.start()
    run_one_job(app)

    assert (app_module.PENDING_DIR / "take-0007.wav").read_bytes() == b"from a previous run"
    assert len(kept_recordings()) == 2


# ------------------------------------------------------------------- shutdown


def test_quitting_waits_for_a_transcription_in_flight(logs, pasted):
    """Quit used to abandon the worker, losing words that were about to arrive."""
    gate = threading.Event()
    transcriber = FakeTranscriber(gate=gate)
    app = build_app(transcriber=transcriber)

    app._spawn_job(audio())
    assert transcriber.started.wait(timeout=5)

    threading.Timer(0.2, gate.set).start()
    app.shutdown()

    assert not app.is_busy
    assert pasted == [TRANSCRIPT]


def test_shutdown_gives_up_rather_than_hanging_forever(logs, pasted, monkeypatch):
    """A stuck upload must not leave a tray icon the user cannot close."""
    monkeypatch.setattr(app_module, "SHUTDOWN_WAIT_SECONDS", 0.1)
    gate = threading.Event()
    transcriber = FakeTranscriber(gate=gate)
    app = build_app(transcriber=transcriber)

    app._spawn_job(audio())
    assert transcriber.started.wait(timeout=5)

    app.shutdown()
    assert len(kept_recordings()) == 1  # the words survive the abandoned job

    gate.set()
    app._wait_for_jobs(5)


def test_shutdown_twice_does_not_wait_twice(logs, pasted, monkeypatch):
    """It is called from the tray menu and again from main()'s finally block.

    Without the guard the user waits out the whole timeout a second time, with the tray
    icon already gone — the app looks closed while the process lingers.
    """
    wait = 0.4
    monkeypatch.setattr(app_module, "SHUTDOWN_WAIT_SECONDS", wait)
    gate = threading.Event()
    app = build_app(transcriber=FakeTranscriber(gate=gate))

    app._spawn_job(audio())
    assert app._transcriber.started.wait(timeout=5)

    first_started = time.monotonic()
    app.shutdown()  # waits out the timeout, then gives up
    assert time.monotonic() - first_started >= wait

    second_started = time.monotonic()
    app.shutdown()
    assert time.monotonic() - second_started < wait / 2  # returned at once

    gate.set()
    app._wait_for_jobs(5)


def test_shutdown_releases_the_microphone_and_the_keyboard_hook(logs):
    recorder = FakeRecorder()
    recorder.is_recording = True
    hotkey = FakeHotkey()
    app = App(
        make_config(),
        recorder=recorder,
        transcriber=FakeTranscriber(),
        usage=FakeUsage(),
        hotkey=hotkey,
    )
    app.attach_tray(FakeTray())

    app.shutdown()

    assert recorder.cancelled
    assert hotkey.stopped


# ------------------------------------------------------------- state bookkeeping


def test_a_finished_job_does_not_paint_idle_over_an_active_recording(logs, pasted):
    """The icon must describe what is happening, not what a worker thread just finished."""
    recorder = FakeRecorder()
    tray = FakeTray()
    app = build_app(recorder=recorder, tray=tray)

    recorder.is_recording = True  # the user started a new take meanwhile
    run_one_job(app)

    assert tray.states[-1] is TrayState.RECORDING


def test_the_error_icon_does_not_clear_while_a_job_is_still_running(logs, pasted):
    """The old timer only looked at the microphone, so it cleared during a queued job."""
    gate = threading.Event()
    tray = FakeTray()
    app = build_app(transcriber=FakeTranscriber(gate=gate), tray=tray)

    app._spawn_job(audio())
    assert app._transcriber.started.wait(timeout=5)

    app._reset_after_error()  # what the 6-second timer would do
    assert TrayState.IDLE not in tray.states

    gate.set()
    assert app._wait_for_jobs(5)


# ------------------------------------------------- what the window shows and controls


def test_the_window_reports_each_state_in_turn(logs, pasted):
    gate = threading.Event()
    recorder = FakeRecorder()
    app = build_app(recorder=recorder, transcriber=FakeTranscriber(gate=gate))

    assert app.ui_state() == "idle"

    app.toggle_recording()
    assert app.ui_state() == "recording"

    app.toggle_pause()
    assert app.ui_state() == "paused"

    app.toggle_pause()
    assert app.ui_state() == "recording"

    app.toggle_recording()
    assert app._transcriber.started.wait(timeout=5)
    assert app.ui_state() == "transcribing"

    gate.set()
    assert app._wait_for_jobs(5)
    assert app.ui_state() == "idle"


def test_the_window_button_starts_and_stops_the_same_way_the_key_does(logs, pasted):
    app = build_app()

    app.toggle_recording()
    app.toggle_recording()
    assert app._wait_for_jobs(5)

    assert pasted == [TRANSCRIPT]


def test_pausing_does_not_throw_the_take_away(logs, pasted):
    """Pause has to keep what was said — otherwise it is just a slower cancel."""
    recorder = FakeRecorder()
    app = build_app(recorder=recorder)

    app.toggle_recording()
    app.toggle_pause()
    assert recorder.is_recording  # still a take in progress
    assert not recorder.cancelled

    app.toggle_pause()
    app.toggle_recording()
    assert app._wait_for_jobs(5)
    assert pasted == [TRANSCRIPT]


def test_pausing_stops_the_auto_stop_clock(logs, pasted):
    """Otherwise a take paused for five minutes would be cut off on resume."""
    app = build_app()

    app.toggle_recording()
    assert app._auto_stop is not None

    app.toggle_pause()
    assert app._auto_stop is None

    app.toggle_pause()
    assert app._auto_stop is not None


def test_the_power_button_stops_the_hotkey_and_the_recording(logs, pasted):
    recorder = FakeRecorder()
    hotkey = FakeHotkey()
    app = App(
        make_config(),
        recorder=recorder,
        transcriber=FakeTranscriber(),
        usage=FakeUsage(),
        hotkey=hotkey,
    )
    app.attach_tray(FakeTray())

    app.toggle_recording()
    app.toggle_enabled()

    assert app.ui_state() == "disabled"
    assert hotkey.stopped
    assert recorder.cancelled
    assert pasted == []  # nothing was transcribed, nothing was paid for


def test_the_power_button_switches_listening_back_on(logs, pasted):
    hotkey = FakeHotkey()
    app = App(
        make_config(),
        recorder=FakeRecorder(),
        transcriber=FakeTranscriber(),
        usage=FakeUsage(),
        hotkey=hotkey,
    )
    app.attach_tray(FakeTray())

    app.toggle_enabled()
    app.toggle_enabled()

    assert app.ui_state() == "idle"
    assert hotkey.starts == 1


def test_the_record_button_does_nothing_while_switched_off(logs, pasted):
    app = build_app()
    app.toggle_enabled()

    app.toggle_recording()

    assert app.ui_state() == "disabled"
    assert pasted == []


def test_the_cancel_button_spends_nothing(logs, pasted):
    transcriber = FakeTranscriber()
    app = build_app(transcriber=transcriber)

    app.toggle_recording()
    app.cancel_recording()

    assert app.ui_state() == "idle"
    assert transcriber.calls == 0
    assert kept_recordings() == []


def test_an_error_shows_in_the_window_and_then_clears(logs, pasted):
    app = build_app(transcriber=FakeTranscriber(error=TranscriptionError("offline")))
    run_one_job(app)

    assert app.ui_state() == "error"

    app._reset_after_error()
    assert app.ui_state() == "idle"


def test_a_clip_too_short_to_be_speech_is_dropped_without_paying(logs, pasted):
    """An accidental key tap must not become a billed API call."""
    recorder = FakeRecorder()
    recorder.stop = lambda: Recording(wav_bytes=b"", duration_seconds=0.05)
    recorder.is_recording = True
    transcriber = FakeTranscriber()
    usage = FakeUsage()
    app = build_app(transcriber=transcriber, recorder=recorder, usage=usage)

    app._stop_recording()
    # Wait for a worker in case the guard is gone — asserting straight away would race
    # the thread and pass even with the guard removed.
    assert app._wait_for_jobs(5)

    assert transcriber.calls == 0
    assert usage.charged == []
    assert pasted == []
    assert kept_recordings() == []


def test_a_normal_clip_is_charged_for(logs, pasted):
    """The other half of the guard: a real dictation must reach the cost tally."""
    usage = FakeUsage()
    app = build_app(usage=usage)
    run_one_job(app, audio(seconds=2.0))
    assert usage.charged == [2.0]


def test_the_duration_elevenlabs_billed_wins_over_the_local_measurement(logs, pasted):
    """The invoice is written from their figure, so the tally should be too."""
    usage = FakeUsage()
    app = build_app(
        transcriber=FakeTranscriber(result=Transcript(text=TRANSCRIPT, billed_seconds=3.5)),
        usage=usage,
    )
    run_one_job(app, audio(seconds=2.0))
    assert usage.charged == [3.5]


def test_the_cost_line_in_the_menu_is_refreshed(logs, pasted):
    """pystray reuses its menu handle, so without this the figure never changes."""
    tray = FakeTray()
    app = build_app(tray=tray)
    run_one_job(app)
    assert tray.menu_refreshes == 1


def test_an_unexpected_failure_is_reported_instead_of_hanging(logs, pasted):
    """Anything not caught inside the worker used to leave the icon amber forever."""
    tray = FakeTray()
    app = build_app(transcriber=FakeTranscriber(error=RuntimeError("something odd")), tray=tray)
    run_one_job(app)

    assert tray.states[-1] is TrayState.ERROR
    assert tray.messages  # the user is told, rather than left waiting
    assert len(kept_recordings()) == 1  # and the audio is still there to retry


def test_two_retries_at_once_cannot_paste_the_same_words_twice(logs, pasted):
    """Selection and claim happen under one lock; doing them apart allowed a double paste."""
    app = build_app(transcriber=FakeTranscriber(error=TranscriptionError("offline")))
    run_one_job(app)
    assert len(kept_recordings()) == 1

    app._transcriber.error = None
    app._transcriber.calls = 0

    barrier = threading.Barrier(2)

    def retry():
        barrier.wait(timeout=5)
        app.retry_last()

    threads = [threading.Thread(target=retry) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert app._wait_for_jobs(5)

    assert app._transcriber.calls == 1
    assert pasted == [TRANSCRIPT]


def test_retry_bills_the_audio_not_the_wav_header(logs, pasted):
    """Measuring a WAV file as raw samples counts its 44-byte header as speech."""
    usage = FakeUsage()
    app = build_app(
        transcriber=FakeTranscriber(error=TranscriptionError("offline")),
        usage=usage,
    )
    run_one_job(app, audio(seconds=2.0))

    app._transcriber.error = None
    app.retry_last()
    assert app._wait_for_jobs(5)

    assert usage.charged == [pytest.approx(2.0)]


def test_old_recordings_are_pruned_at_startup(logs, pasted):
    """Otherwise failed takes accumulate on disk forever."""
    app_module.PENDING_DIR.mkdir(parents=True)
    stale = app_module.PENDING_DIR / "take-0001.wav"
    fresh = app_module.PENDING_DIR / "take-0002.wav"
    stale.write_bytes(b"old")
    fresh.write_bytes(b"new")

    eight_days_ago = time.time() - 8 * 86_400
    os.utime(stale, (eight_days_ago, eight_days_ago))

    build_app().start()

    assert not stale.exists()
    assert fresh.exists()


def test_a_take_started_from_the_window_says_so_when_it_is_pasted(logs, monkeypatch):
    """Clicking the record button is the only thing that moves the focus. On macOS that
    fact is what decides whether pasting is safe when the system cannot be asked where
    the focus went, so it has to travel with the take."""
    seen: list[bool] = []
    monkeypatch.setattr(
        app_module,
        "inject_text",
        lambda text, **kwargs: seen.append(kwargs["started_from_our_window"]),
    )
    app = build_app()

    app.toggle_recording()
    app.toggle_recording()
    assert app._wait_for_jobs(5)

    assert seen == [True]


def test_a_take_started_with_the_hotkey_says_so_too(logs, monkeypatch):
    seen: list[bool] = []
    monkeypatch.setattr(
        app_module,
        "inject_text",
        lambda text, **kwargs: seen.append(kwargs["started_from_our_window"]),
    )
    app = build_app()

    app._handle_action(Action.START)
    app._handle_action(Action.STOP)
    assert app._wait_for_jobs(5)

    assert seen == [False]


# ------------------------------------------------------------------ the summary mode


def test_the_words_go_out_alone_in_the_ordinary_mode(logs, pasted):
    app = build_app()
    run_one_job(app)
    assert pasted == [TRANSCRIPT]


def test_summary_mode_puts_the_instruction_in_front_of_the_words(logs, pasted):
    """The app asks no model anything. The instruction rides along to whatever receives
    the paste, which is why the transcript has to survive underneath it, word for word."""
    app = build_app(make_config(summary_instruction="შეაჯამე:"))
    app.toggle_summary_mode()

    run_one_job(app)

    assert pasted == [f"შეაჯამე:\n\n{TRANSCRIPT}"]


def test_switching_the_mode_back_pastes_the_words_again(logs, pasted):
    app = build_app(make_config(summary_instruction="შეაჯამე:"))
    app.toggle_summary_mode()
    app.toggle_summary_mode()

    run_one_job(app)

    assert pasted == [TRANSCRIPT]


def test_the_mode_the_window_remembered_is_the_one_that_is_used(logs, pasted):
    app = build_app(make_config(summary_instruction="შეაჯამე:"))
    app.set_summary_mode(True)

    run_one_job(app)

    assert pasted[0].startswith("შეაჯამე:")
    assert app.ui_summary_mode() is True


def test_a_recording_of_only_hesitation_keeps_its_audio_and_pastes_nothing(logs, pasted):
    """Nothing reached the cursor, so nothing was dictated — the take is not a success."""
    tray = FakeTray()
    app = build_app(transcriber=FakeTranscriber(error=NothingToPasteError("only ums")), tray=tray)

    run_one_job(app)

    assert pasted == []
    assert len(kept_recordings()) == 1
    assert tray.messages


def test_a_hesitation_only_take_is_not_reported_as_a_transcription_failure(logs, pasted):
    """It is a different thing from "the upload failed", and the message has to say so —
    otherwise the user goes looking for a problem that is not there."""
    tray = FakeTray()
    app = build_app(transcriber=FakeTranscriber(error=NothingToPasteError("only ums")), tray=tray)

    run_one_job(app)

    assert tray.states[-1] is TrayState.ERROR  # the state machine still recovers
    assert TRANSCRIPT not in " ".join(tray.messages)
    # The exact sentence, not just "some message": under the generic handler the user is
    # told the recording was saved and can be re-sent, which for a take of pure "მმმ"
    # sends them looking for a problem that is not there.
    assert tray.messages[-1] == "მხოლოდ ჩაფიქრების ხმა იყო — ჩასასმელი არაფერია"
