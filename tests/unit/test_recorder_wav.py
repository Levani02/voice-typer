"""WAV assembly and duration arithmetic — the parts of recording that need no microphone."""

import io
import wave

import pytest

from voice_typer.recorder import build_wav, pcm_duration_seconds

SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2


def one_second_of_silence(sample_rate: int = SAMPLE_RATE) -> bytes:
    return b"\x00\x00" * sample_rate


def test_wav_header_matches_the_capture_format():
    wav_bytes = build_wav(one_second_of_silence(), SAMPLE_RATE)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == BYTES_PER_SAMPLE
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnframes() == SAMPLE_RATE


def test_samples_survive_the_round_trip():
    pcm = bytes(range(256)) * 4
    wav_bytes = build_wav(pcm, SAMPLE_RATE)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.readframes(wav.getnframes()) == pcm


def test_an_empty_capture_still_produces_a_valid_file():
    """A key tapped by accident yields no samples; it must not crash the WAV writer."""
    wav_bytes = build_wav(b"", SAMPLE_RATE)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.getnframes() == 0


@pytest.mark.parametrize(
    ("samples", "sample_rate", "expected_seconds"),
    [
        (0, SAMPLE_RATE, 0.0),
        (SAMPLE_RATE, SAMPLE_RATE, 1.0),
        (SAMPLE_RATE * 3, SAMPLE_RATE, 3.0),
        (8_000, 16_000, 0.5),
        (48_000, 48_000, 1.0),
    ],
    ids=["empty", "one-second", "three-seconds", "half-second", "48kHz"],
)
def test_duration(samples, sample_rate, expected_seconds):
    pcm = b"\x00\x00" * samples
    assert pcm_duration_seconds(pcm, sample_rate) == pytest.approx(expected_seconds)


def test_a_short_tap_falls_under_the_discard_threshold():
    """100 ms of audio is a mis-press, not speech — the app drops clips this short."""
    pcm = b"\x00\x00" * (SAMPLE_RATE // 10)
    assert pcm_duration_seconds(pcm, SAMPLE_RATE) * 1000 < 300
