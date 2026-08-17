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

# If the audio callback has not fired for this long, the device has almost certainly gone
# away. PortAudio offers no notification for that — the callback just stops.
STALE_AUDIO_SECONDS = 2.0


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


def wav_duration_seconds(wav_bytes: bytes) -> float:
    """How long a complete WAV file plays for, read from its own header.

    Used when re-sending a recording from disk: measuring those bytes as raw samples would
    count the 44-byte header as audio and bill for it, and would use the wrong sample rate
    entirely if the setting changed between recording and retry.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            return wav.getnframes() / wav.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError) as exc:
        raise RecorderError(f"that file is not a readable recording: {exc}") from exc


class Recorder:
    """Owns the input stream. Exactly one capture may be in flight at a time."""

    def __init__(self, sample_rate: int, device: int | str | None = None) -> None:
        self._sample_rate = sample_rate
        self._device = device
        self._stream: sd.InputStream | None = None
        self._chunks: list[bytes] = []
        self._started_at: float | None = None
        self._last_audio_at: float | None = None
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
                    finished_callback=self._on_stream_finished,
                )
                stream.start()
            except Exception as exc:  # sounddevice raises several unrelated types
                raise RecorderError(f"could not open the microphone: {exc}") from exc

            self._stream = stream
            self._started_at = time.monotonic()
            self._last_audio_at = self._started_at
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
        """Release the stream on every path, then hand back the raw samples.

        `abort` rather than `stop`: stop waits for the device to drain, and on a device
        that has been unplugged PortAudio can wait forever. Every sample has already been
        collected by the callback, so there is nothing to drain. `ignore_errors=True` on
        both calls means a dead device cannot raise on the way out either.
        """
        with self._lock:
            stream, self._stream = self._stream, None
            self._started_at = None
            chunks, self._chunks = self._chunks, []
            last_audio_at = self._last_audio_at

        if stream is not None:
            try:
                stream.abort(ignore_errors=True)
            finally:
                stream.close(ignore_errors=True)

        if last_audio_at is not None and time.monotonic() - last_audio_at > STALE_AUDIO_SECONDS:
            # PortAudio has no device-removal notification: when a microphone disappears
            # the callback simply stops being called. Silence in the log is the only clue.
            logger.warning(
                "no audio arrived for over %.1fs before stopping — the microphone may have "
                "been unplugged or changed",
                STALE_AUDIO_SECONDS,
            )

        return b"".join(chunks)

    def _on_audio(self, indata, _frames: int, _time_info, status) -> None:
        """PortAudio callback. Runs on the audio thread — keep it short and allocation-free."""
        if status:
            logger.warning("audio input status: %s", status)
        self._chunks.append(bytes(indata))
        self._last_audio_at = time.monotonic()

    def _on_stream_finished(self) -> None:
        """PortAudio has abandoned the stream — usually the microphone was unplugged.

        Without this, `is_recording` would stay True forever and the hotkey would appear
        to do nothing: every press would be treated as 'already recording'.
        """
        with self._lock:
            if self._stream is None:
                return  # the ordinary path — `_close_stream` already took ownership
            self._stream = None
            self._started_at = None
        logger.warning("the audio device stopped on its own — the microphone may be gone")
