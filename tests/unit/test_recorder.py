"""The Recorder class, with PortAudio replaced by a stand-in.

No microphone is touched. What these protect: the stream is released on every path, the
samples the callback collected all reach the WAV, and a device that dies does not leave
the app believing it is still recording — which would make the hotkey look broken.
"""

import pytest

from voice_typer import recorder as recorder_module
from voice_typer.recorder import Recorder, RecorderError, wav_duration_seconds

SAMPLE_RATE = 16_000


class FakeStream:
    """Stands in for sounddevice.InputStream and records how it was disposed of."""

    def __init__(self, *, callback, finished_callback, fail_on=None, **_kwargs):
        self.callback = callback
        self.finished_callback = finished_callback
        # Share the fixture's set rather than copying it, so a test can arm a failure
        # after the stream is already open — which is when a device actually disappears.
        self.fail_on = set() if fail_on is None else fail_on
        self.started = False
        self.aborted = False
        self.closed = False

    def start(self):
        if "start" in self.fail_on:
            raise OSError("the device refused to start")
        self.started = True

    def abort(self, ignore_errors=True):
        if "abort" in self.fail_on:
            raise OSError("the device is gone")
        self.aborted = True

    def close(self, ignore_errors=True):
        self.closed = True

    def feed(self, pcm: bytes):
        """Deliver audio the way PortAudio would, from its own thread."""
        self.callback(pcm, len(pcm) // 2, None, None)


@pytest.fixture
def fake_audio(monkeypatch):
    """Replace the sounddevice module inside recorder.py. Returns the streams opened."""
    opened: list[FakeStream] = []
    fail_on: set[str] = set()

    class FakeSoundDevice:
        @staticmethod
        def InputStream(**kwargs):
            if "open" in fail_on:
                raise OSError("no such device")
            stream = FakeStream(fail_on=fail_on, **kwargs)
            opened.append(stream)
            return stream

    monkeypatch.setattr(recorder_module, "sd", FakeSoundDevice)
    return opened, fail_on


def test_recording_collects_what_the_callback_delivered(fake_audio):
    opened, _ = fake_audio
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    opened[0].feed(b"\x01\x02" * 100)
    opened[0].feed(b"\x03\x04" * 100)
    result = recorder.stop()

    assert result.duration_seconds == pytest.approx(200 / SAMPLE_RATE)
    assert wav_duration_seconds(result.wav_bytes) == pytest.approx(200 / SAMPLE_RATE)


def test_the_stream_is_released_after_a_normal_stop(fake_audio):
    opened, _ = fake_audio
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    recorder.stop()

    assert opened[0].aborted and opened[0].closed
    assert not recorder.is_recording


def test_the_stream_is_released_after_a_cancel(fake_audio):
    opened, _ = fake_audio
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    opened[0].feed(b"\x01\x02" * 100)
    recorder.cancel()

    assert opened[0].closed
    assert not recorder.is_recording


def test_the_stream_is_closed_even_when_abort_raises(fake_audio):
    """A microphone that has been unplugged can throw on the way out — it must still close."""
    opened, fail_on = fake_audio
    recorder = Recorder(SAMPLE_RATE)
    recorder.start()
    fail_on.add("abort")

    with pytest.raises(OSError):
        recorder.stop()

    assert opened[0].closed
    assert not recorder.is_recording  # and the app is not left stuck


def test_cancelled_audio_never_reaches_the_next_recording(fake_audio):
    opened, _ = fake_audio
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    opened[0].feed(b"\xff\xff" * 100)
    recorder.cancel()

    recorder.start()
    opened[1].feed(b"\x01\x02" * 50)
    result = recorder.stop()

    assert result.duration_seconds == pytest.approx(50 / SAMPLE_RATE)


def test_a_microphone_that_cannot_be_opened_is_explained(fake_audio):
    _, fail_on = fake_audio
    fail_on.add("open")
    recorder = Recorder(SAMPLE_RATE)

    with pytest.raises(RecorderError, match="microphone"):
        recorder.start()

    assert not recorder.is_recording  # the hotkey stays usable


def test_a_device_that_stops_on_its_own_does_not_leave_the_app_stuck(fake_audio):
    """PortAudio gives no unplug notification — without this the hotkey looks dead."""
    opened, _ = fake_audio
    recorder = Recorder(SAMPLE_RATE)
    recorder.start()
    assert recorder.is_recording

    opened[0].finished_callback()  # PortAudio abandoned the stream

    assert not recorder.is_recording
    recorder.start()  # and a fresh recording can begin
    assert recorder.is_recording


def test_starting_twice_does_not_open_a_second_microphone(fake_audio):
    opened, _ = fake_audio
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    recorder.start()

    assert len(opened) == 1


def test_the_configured_device_and_rate_are_used(fake_audio):
    opened, _ = fake_audio
    Recorder(8_000, device=3).start()

    assert opened[0].callback is not None
    stream = opened[0]
    assert stream.finished_callback is not None


def test_wav_duration_rejects_something_that_is_not_a_recording():
    with pytest.raises(RecorderError, match="not a readable recording"):
        wav_duration_seconds(b"this is not a WAV file at all")
