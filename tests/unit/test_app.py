"""The state machine — the part most likely to lose the user's words.

Every collaborator is a stand-in, so these run with no microphone, no keyboard hook and
no network. What they protect: a recording is on disk before it is uploaded and gone only
once the text has landed, the tray never describes a job that is not the live one, and no
two jobs ever paste over each other.
"""

import threading

import pytest

from voice_typer import app as app_module
from voice_typer.app import App
from voice_typer.config import Config
from voice_typer.injector import ClipboardUnavailableError, PasteFailedError
from voice_typer.recorder import Recording, build_wav
from voice_typer.transcriber import TranscriptionError, Usage
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
        "price_per_hour_usd": 0.22,
        "log_transcripts": False,
    }
    return Config(**{**values, **overrides})


def two_seconds_of_audio() -> Recording:
    pcm = b"\x00\x00" * (SAMPLE_RATE * 2)
    return Recording(wav_bytes=build_wav(pcm, SAMPLE_RATE), duration_seconds=2.0)


class FakeRecorder:
    def __init__(self):
        self.is_recording = False
        self.cancelled = False

    def start(self):
        self.is_recording = True

    def stop(self):
        self.is_recording = False
        return two_seconds_of_audio()

    def cancel(self):
        self.is_recording = False
        self.cancelled = True


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
        return self.result


class FakeUsage:
    def add(self, seconds):
        return Usage(calls=1, total_seconds=seconds, total_cost_usd=0.0)

    def read(self):
        return Usage(calls=0, total_seconds=0.0, total_cost_usd=0.0)


class FakeHotkeyLogic:
    def force_idle(self):
        pass


class FakeHotkey:
    def __init__(self):
        self.logic = FakeHotkeyLogic()
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True


class FakeTray:
    def __init__(self):
        self.states: list[TrayState] = []
        self.messages: list[str] = []

    def set_state(self, state):
        self.states.append(state)

    def notify(self, message, title=None):
        self.messages.append(message)


@pytest.fixture
def logs(tmp_path, monkeypatch):
    """Point every on-disk path at a temporary folder."""
    monkeypatch.setattr(app_module, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(app_module, "LAST_RECORDING_PATH", tmp_path / "last_recording.wav")
    monkeypatch.setattr(app_module, "LAST_TRANSCRIPT_PATH", tmp_path / "last_transcript.txt")
    return tmp_path


@pytest.fixture
def pasted(monkeypatch):
    """Capture what would have been pasted, instead of touching the real clipboard."""
    captured: list[str] = []
    monkeypatch.setattr(app_module, "inject_text", lambda text, **kwargs: captured.append(text))
    return captured


def build_app(config=None, transcriber=None, tray=None, recorder=None):
    app = App(
        config or make_config(),
        recorder=recorder or FakeRecorder(),
        transcriber=transcriber or FakeTranscriber(),
        usage=FakeUsage(),
        hotkey=FakeHotkey(),
    )
    app.attach_tray(tray or FakeTray())
    return app


def run_one_job(app, recording=None):
    """Dictate once, synchronously — the worker is awaited before returning."""
    app._spawn_job(recording or two_seconds_of_audio())
    assert app._wait_for_jobs(5), "the worker did not finish"


# --------------------------------------------------------------- the happy path


def test_a_successful_dictation_pastes_the_text(logs, pasted):
    app = build_app()
    run_one_job(app)
    assert pasted == [TRANSCRIPT]


def test_the_recording_is_deleted_once_the_text_has_landed(logs, pasted):
    app = build_app()
    run_one_job(app)
    assert not app_module.LAST_RECORDING_PATH.exists()


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

    app._spawn_job(two_seconds_of_audio())
    assert transcriber.started.wait(timeout=5)
    assert app_module.LAST_RECORDING_PATH.exists()  # already saved, upload still running

    gate.set()
    assert app._wait_for_jobs(5)


def test_a_failed_transcription_keeps_the_recording(logs, pasted):
    app = build_app(transcriber=FakeTranscriber(error=TranscriptionError("no network")))
    run_one_job(app)

    assert app_module.LAST_RECORDING_PATH.exists()
    assert pasted == []


def test_a_refused_paste_keeps_the_recording(logs, monkeypatch):
    """The text is on the clipboard, but until it is in a window the audio stays."""
    monkeypatch.setattr(
        app_module, "inject_text", _raiser(PasteFailedError("the window refused it"))
    )
    app = build_app()
    run_one_job(app)
    assert app_module.LAST_RECORDING_PATH.exists()


def test_an_unusable_clipboard_writes_the_text_to_a_file(logs, monkeypatch):
    """Ctrl+V would find nothing here, so the words have to go somewhere the user can reach."""
    monkeypatch.setattr(
        app_module, "inject_text", _raiser(ClipboardUnavailableError("clipboard locked"))
    )
    app = build_app()
    run_one_job(app)

    assert app_module.LAST_TRANSCRIPT_PATH.read_text(encoding="utf-8") == TRANSCRIPT


def test_the_two_paste_failures_give_opposite_advice(logs, monkeypatch):
    """Telling someone to press Ctrl+V when the clipboard write failed sends them nowhere."""
    tray = FakeTray()
    monkeypatch.setattr(app_module, "inject_text", _raiser(PasteFailedError("refused")))
    run_one_job(build_app(tray=tray))
    assert "Ctrl+V" in tray.messages[-1]

    tray = FakeTray()
    monkeypatch.setattr(app_module, "inject_text", _raiser(ClipboardUnavailableError("locked")))
    run_one_job(build_app(tray=tray))
    assert "Ctrl+V" not in tray.messages[-1]


def _raiser(exc):
    def fail(_text, **_kwargs):
        raise exc

    return fail


# ------------------------------------------------------------ overlapping jobs


def test_retry_is_refused_while_a_job_is_running(logs, pasted):
    """Two clicks on Retry used to paste the same words twice."""
    gate = threading.Event()
    transcriber = FakeTranscriber(gate=gate)
    tray = FakeTray()
    app = build_app(transcriber=transcriber, tray=tray)

    app._spawn_job(two_seconds_of_audio())
    assert transcriber.started.wait(timeout=5)

    app.retry_last()  # the second click, while the first is still in flight

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

    app._spawn_job(two_seconds_of_audio())
    assert transcriber.started.wait(timeout=5)
    app._spawn_job(two_seconds_of_audio())  # queued behind the first

    gate.set()
    assert app._wait_for_jobs(5)

    assert transcriber.calls == 2
    assert tray.states[-1] is TrayState.IDLE
    # TRANSCRIBING is asserted again when the queued job actually begins
    assert tray.states.count(TrayState.TRANSCRIBING) >= 3


def test_retry_says_so_when_there_is_nothing_to_retry(logs):
    tray = FakeTray()
    app = build_app(tray=tray)
    app.retry_last()
    assert tray.messages


def test_retry_resends_a_kept_recording(logs, pasted):
    app_module.LAST_RECORDING_PATH.write_bytes(build_wav(b"\x00\x00" * SAMPLE_RATE, SAMPLE_RATE))
    app = build_app()

    app.retry_last()
    assert app._wait_for_jobs(5)
    assert pasted == [TRANSCRIPT]


# ------------------------------------------------------------------- shutdown


def test_quitting_waits_for_a_transcription_in_flight(logs, pasted):
    """Quit used to abandon the worker, losing words that were about to arrive."""
    gate = threading.Event()
    transcriber = FakeTranscriber(gate=gate)
    app = build_app(transcriber=transcriber)

    app._spawn_job(two_seconds_of_audio())
    assert transcriber.started.wait(timeout=5)

    threading.Timer(0.2, gate.set).start()
    app.shutdown()

    assert not app.is_busy
    assert pasted == [TRANSCRIPT]


def test_shutdown_gives_up_rather_than_hanging_forever(logs, pasted, monkeypatch):
    """A stuck upload must not leave a window the user cannot close."""
    monkeypatch.setattr(app_module, "SHUTDOWN_WAIT_SECONDS", 0.1)
    gate = threading.Event()
    transcriber = FakeTranscriber(gate=gate)
    app = build_app(transcriber=transcriber)

    app._spawn_job(two_seconds_of_audio())
    assert transcriber.started.wait(timeout=5)

    app.shutdown()  # returns despite the worker still being blocked
    assert app_module.LAST_RECORDING_PATH.exists()  # the words survive the abandoned job

    gate.set()
    app._wait_for_jobs(5)


def test_shutdown_releases_the_microphone_and_the_keyboard_hook(logs):
    recorder = FakeRecorder()
    recorder.is_recording = True
    app = App(
        make_config(),
        recorder=recorder,
        transcriber=FakeTranscriber(),
        usage=FakeUsage(),
        hotkey=(hotkey := FakeHotkey()),
    )
    app.attach_tray(FakeTray())

    app.shutdown()

    assert recorder.cancelled
    assert hotkey.stopped


# ------------------------------------------------------------- state bookkeeping


def test_the_error_icon_does_not_clear_while_a_job_is_still_running(logs, pasted):
    """The old timer only looked at the microphone, so it cleared during a queued job."""
    gate = threading.Event()
    tray = FakeTray()
    app = build_app(transcriber=FakeTranscriber(gate=gate), tray=tray)

    app._spawn_job(two_seconds_of_audio())
    assert app._transcriber.started.wait(timeout=5)

    app._reset_to_idle()  # what the 6-second timer would do
    assert TrayState.IDLE not in tray.states

    gate.set()
    assert app._wait_for_jobs(5)


def test_a_clip_too_short_to_be_speech_is_dropped_without_paying(logs, pasted):
    recorder = FakeRecorder()
    recorder.stop = lambda: Recording(wav_bytes=b"", duration_seconds=0.05)
    recorder.is_recording = True
    transcriber = FakeTranscriber()
    app = build_app(transcriber=transcriber, recorder=recorder)

    app._stop_recording()

    assert transcriber.calls == 0
    assert pasted == []
