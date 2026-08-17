"""Microphone capture into a WAV held in memory.

16 kHz mono 16-bit PCM — the smallest format that loses nothing for speech, which keeps
the upload to ElevenLabs fast. Nothing is written to disk here; that only happens if an
upload fails, and it is `app.py` that decides to do it.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from dataclasses import dataclass

import sounddevice as sd

logger = logging.getLogger(__name__)

CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2  # int16


class RecorderError(Exception):
    """The microphone could not be opened or read. The message is shown to the user."""


@dataclass(frozen=True)
class Recording:
    """A finished capture, ready to upload."""

    wav_bytes: bytes
    duration_seconds: float


def build_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw mono int16 samples in a WAV container, in memory."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def pcm_duration_seconds(pcm: bytes, sample_rate: int) -> float:
    """How long the raw samples play for."""
    frames = len(pcm) / (CHANNELS * SAMPLE_WIDTH_BYTES)
    return frames / sample_rate


class Recorder:
    """Owns the input stream. Exactly one capture may be in flight at a time."""

    def __init__(self, sample_rate: int, device: int | str | None = None) -> None:
        self._sample_rate = sample_rate
        self._device = device
        self._stream: sd.InputStream | None = None
        self._chunks: list[bytes] = []
        self._started_at: float | None = None
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def elapsed_seconds(self) -> float:
        """Seconds since `start`, or 0.0 when idle. Used for the auto-stop ceiling."""
        return 0.0 if self._started_at is None else time.monotonic() - self._started_at

    def start(self) -> None:
        """Open the microphone and begin collecting samples."""
        with self._lock:
            if self._stream is not None:
                return
            self._chunks = []
            try:
                stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=CHANNELS,
                    dtype="int16",
                    device=self._device,
                    callback=self._on_audio,
                )
                stream.start()
            except Exception as exc:  # sounddevice raises several unrelated types
                raise RecorderError(f"could not open the microphone: {exc}") from exc

            self._stream = stream
            self._started_at = time.monotonic()
            logger.info("recording started (device=%s, %d Hz)", self._device, self._sample_rate)

    def stop(self) -> Recording:
        """Close the microphone and return what was captured."""
        pcm = self._close_stream()
        duration = pcm_duration_seconds(pcm, self._sample_rate)
        logger.info("recording stopped (%.1f s)", duration)
        return Recording(wav_bytes=build_wav(pcm, self._sample_rate), duration_seconds=duration)

    def cancel(self) -> None:
        """Close the microphone and throw the samples away. Nothing is uploaded."""
        self._close_stream()
        logger.info("recording cancelled by the user")

    def _close_stream(self) -> bytes:
        """Stop and release the stream on every path, then hand back the raw samples."""
        with self._lock:
            stream, self._stream = self._stream, None
            self._started_at = None
            chunks, self._chunks = self._chunks, []

        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

        return b"".join(chunks)

    def _on_audio(self, indata, _frames: int, _time_info, status) -> None:
        """PortAudio callback. Runs on the audio thread — keep it short and allocation-free."""
        if status:
            logger.warning("audio input status: %s", status)
        self._chunks.append(bytes(indata))
