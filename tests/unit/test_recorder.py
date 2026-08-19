"""The Recorder class, with PortAudio replaced by a stand-in.

No microphone is touched. What these protect: the stream is released on every path, the
samples the callback collected all reach the WAV, and a device that dies does not leave
the app believing it is still recording — which would make the hotkey look broken.
"""

import time
from typing import ClassVar

import pytest

from voice_typer import recorder as recorder_module
from voice_typer.recorder import Recorder, RecorderError, wav_duration_seconds

SAMPLE_RATE = 16_000


class FakeStream:
    """Stands in for sounddevice.InputStream and records how it was disposed of."""

    def __init__(self, *, callback, finished_callback, fail_on=None, **kwargs):
        self.callback = callback
        self.finished_callback = finished_callback
        # The rate the stream was actually opened at, which is not always the one asked
        # for — see the sample-rate tests at the bottom of this file.
        self.samplerate = kwargs.get("samplerate")
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
        # A device that runs at 48 kHz and refuses everything else is not a contrivance:
        # it is every MacBook's built-in microphone. `unsupported_rates` is what CoreAudio
        # does to a rate the hardware cannot do — reject it rather than resample.
        native_rate = 48_000
        unsupported_rates: ClassVar[set[int]] = set()

        @staticmethod
        def InputStream(**kwargs):
            if "open" in fail_on:
                raise OSError("no such device")
            if kwargs.get("samplerate") in FakeSoundDevice.unsupported_rates:
                raise OSError("Invalid sample rate")
            stream = FakeStream(fail_on=fail_on, **kwargs)
            opened.append(stream)
            return stream

        @staticmethod
        def query_devices(_device, _kind):
            return {
                "name": "Fake microphone",
                "default_samplerate": float(FakeSoundDevice.native_rate),
            }

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


def test_pausing_releases_the_microphone_but_keeps_the_audio(fake_audio):
    """The mic's in-use light going out is the only honest way to show nothing is captured."""
    opened, _ = fake_audio
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    opened[0].feed(b"\x01\x02" * 100)
    recorder.pause()

    assert opened[0].closed  # the device really is released
    assert recorder.is_paused
    assert recorder.is_recording  # but a take is still in progress
    assert recorder.level == 0.0


def test_resuming_carries_on_the_same_take(fake_audio):
    opened, _ = fake_audio
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    opened[0].feed(b"\x01\x02" * 100)
    recorder.pause()
    recorder.resume()
    opened[1].feed(b"\x03\x04" * 100)
    result = recorder.stop()

    assert len(opened) == 2  # a second stream was opened
    assert result.duration_seconds == pytest.approx(200 / SAMPLE_RATE)  # both halves kept


def test_time_spent_paused_is_not_counted(fake_audio):
    """The timer in the window would otherwise keep climbing while nothing is recorded."""
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    recorder.pause()
    paused_at = recorder.elapsed_seconds
    time.sleep(0.15)

    assert recorder.elapsed_seconds == pytest.approx(paused_at, abs=0.01)


def test_pausing_twice_is_harmless(fake_audio):
    opened, _ = fake_audio
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    recorder.pause()
    recorder.pause()

    assert recorder.is_paused
    assert len(opened) == 1


def test_a_new_take_does_not_inherit_the_previous_one_s_audio(fake_audio):
    opened, _ = fake_audio
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    opened[0].feed(b"\xff\xff" * 100)
    recorder.pause()
    recorder.stop()

    recorder.start()
    opened[1].feed(b"\x01\x02" * 50)
    result = recorder.stop()

    assert result.duration_seconds == pytest.approx(50 / SAMPLE_RATE)


def test_the_level_meter_reads_loud_audio_higher_than_quiet(fake_audio):
    opened, _ = fake_audio
    recorder = Recorder(SAMPLE_RATE)
    recorder.start()

    opened[0].feed(b"\x00\x00" * 100)
    quiet = recorder.level
    opened[0].feed(b"\x00\x40" * 100)
    loud = recorder.level

    assert quiet == 0.0
    assert loud > quiet
    assert 0.0 <= loud <= 1.0


def test_wav_duration_rejects_something_that_is_not_a_recording():
    with pytest.raises(RecorderError, match="not a readable recording"):
        wav_duration_seconds(b"this is not a WAV file at all")


# --------------------------------------------------------------- the macOS sample rate
#
# Windows resamples whatever the microphone produces to whatever was asked for, so a
# hard-coded 16 kHz always worked there. macOS does not: a device opens at its own
# hardware rate or it does not open at all. These four hold the fallback in place.


def test_a_microphone_that_refuses_the_configured_rate_records_at_its_own(fake_audio):
    """The bug this file exists to prevent coming back.

    A MacBook's built-in microphone runs at 48 kHz and rejects 16 kHz outright. Before the
    fallback, that surfaced as "could not open the microphone" and dictation was simply
    dead — and because plugging in headphones changes the device, it came and went.
    """
    opened, _ = fake_audio
    recorder_module.sd.unsupported_rates.add(SAMPLE_RATE)
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    opened[0].feed(b"\x01\x02" * 480)
    recorder.stop()

    assert opened[0].samplerate == 48_000


def test_the_wav_is_stamped_with_the_rate_actually_used(fake_audio):
    """A 48 kHz take written with a 16 kHz header plays back three times too slow.

    ElevenLabs would receive a chipmunk recording of Georgian and return nonsense, and the
    cost accounting would bill three times the real duration.
    """
    opened, _ = fake_audio
    recorder_module.sd.unsupported_rates.add(SAMPLE_RATE)
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    opened[0].feed(b"\x01\x02" * 480)
    result = recorder.stop()

    assert wav_duration_seconds(result.wav_bytes) == pytest.approx(480 / 48_000)
    assert result.duration_seconds == pytest.approx(480 / 48_000)


def test_resuming_reopens_at_the_same_rate_the_take_began_with(fake_audio):
    """Two segments at different rates concatenate into garbled audio.

    The samples are joined as raw PCM with no rate information of their own, so a take
    that starts at 48 kHz must stay at 48 kHz even if the first choice would work now.
    """
    opened, _ = fake_audio
    recorder_module.sd.unsupported_rates.add(SAMPLE_RATE)
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    opened[0].feed(b"\x01\x02" * 240)
    recorder.pause()
    recorder_module.sd.unsupported_rates.clear()  # the rate would be accepted now
    recorder.resume()
    opened[1].feed(b"\x03\x04" * 240)
    result = recorder.stop()

    assert opened[1].samplerate == 48_000  # not the 16 kHz that is available again
    assert wav_duration_seconds(result.wav_bytes) == pytest.approx(480 / 48_000)


def test_a_fresh_take_asks_for_the_configured_rate_again(fake_audio):
    """The fallback belongs to one take, not to the app.

    Unplug the 48 kHz device and the next one may well do 16 kHz, which is smaller to
    upload. Sticking with the fallback for ever would quietly cost the user bandwidth.
    """
    opened, _ = fake_audio
    recorder_module.sd.unsupported_rates.add(SAMPLE_RATE)
    recorder = Recorder(SAMPLE_RATE)
    recorder.start()
    recorder.stop()

    recorder_module.sd.unsupported_rates.clear()
    recorder.start()
    recorder.stop()

    assert opened[0].samplerate == 48_000
    assert opened[1].samplerate == SAMPLE_RATE


def test_a_device_that_refuses_every_rate_is_still_explained(fake_audio):
    """The fallback must not swallow a genuinely dead microphone into a confusing error."""
    _, fail_on = fake_audio
    fail_on.add("open")
    recorder = Recorder(SAMPLE_RATE)

    with pytest.raises(RecorderError, match="microphone"):
        recorder.start()

    assert not recorder.is_recording


def test_a_take_is_never_resumed_at_a_different_rate(fake_audio):
    """Resuming at a new rate would splice two speeds into one sentence.

    Found by review, reproduced before it was fixed: the first segment played back three
    times too slow, the transcript came back as nonsense, and the cost accounting billed
    twice the real length. Unplugging a headset while paused is enough to trigger it.
    """
    opened, _ = fake_audio
    recorder_module.sd.unsupported_rates.add(SAMPLE_RATE)
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    opened[0].feed(b"\x01\x02" * 240)
    recorder.pause()
    # The default input changed while paused — the new one does 16 kHz and nothing else,
    # so a fallback would happily open at a rate this take's samples cannot be joined at.
    recorder_module.sd.unsupported_rates.clear()
    recorder_module.sd.unsupported_rates.add(48_000)
    recorder_module.sd.native_rate = SAMPLE_RATE

    with pytest.raises(RecorderError, match="microphone"):
        recorder.resume()

    assert len(opened) == 1  # nothing was opened at a rate this take cannot use
    assert recorder.is_recording  # and the take is still alive, not silently dropped


def test_words_already_spoken_survive_a_resume_that_fails(fake_audio):
    """A microphone that goes away mid-sentence must not take the sentence with it."""
    opened, _ = fake_audio
    recorder_module.sd.unsupported_rates.add(SAMPLE_RATE)
    recorder = Recorder(SAMPLE_RATE)

    recorder.start()
    opened[0].feed(b"\x01\x02" * 240)
    recorder.pause()
    recorder_module.sd.unsupported_rates.clear()
    recorder_module.sd.unsupported_rates.add(48_000)
    recorder_module.sd.native_rate = SAMPLE_RATE
    with pytest.raises(RecorderError):
        recorder.resume()

    result = recorder.stop()  # the user presses stop after the failure

    assert wav_duration_seconds(result.wav_bytes) == pytest.approx(240 / 48_000)
    assert result.duration_seconds == pytest.approx(240 / 48_000)


def test_a_take_killed_by_a_dead_device_does_not_fix_the_rate_for_ever(fake_audio):
    """A take can end without ever draining, and must not leave its rate behind.

    The device vanishing mid-take skips `stop` entirely, so the rate was only cleared on
    the tidy path. Every later recording then ignored config.json and uploaded three
    times more than it needed to.
    """
    opened, _ = fake_audio
    recorder_module.sd.unsupported_rates.add(SAMPLE_RATE)
    recorder = Recorder(SAMPLE_RATE)
    recorder.start()

    opened[0].finished_callback()  # PortAudio abandoned the stream — no stop, no drain
    recorder_module.sd.unsupported_rates.clear()
    recorder.start()

    assert opened[0].samplerate == 48_000
    assert opened[1].samplerate == SAMPLE_RATE  # config.json is honoured again


def test_a_stream_that_cannot_start_is_closed(fake_audio):
    """An opened stream holds the microphone whether it ever started or not.

    The caller keeps no reference to close it by, so it has to be closed where it was
    made — and the fallback means two of them can be created for a single key press.
    """
    opened, fail_on = fake_audio
    fail_on.add("start")
    recorder = Recorder(SAMPLE_RATE)

    with pytest.raises(RecorderError, match="microphone"):
        recorder.start()

    assert len(opened) == 2  # the configured rate, then the device's own
    assert all(stream.closed for stream in opened)  # neither one still holds the device
