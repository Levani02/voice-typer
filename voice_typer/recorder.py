"""Microphone capture into a WAV held in memory.

16 kHz mono 16-bit PCM — the smallest format that loses nothing for speech, which keeps
the upload to ElevenLabs fast. Nothing is written to disk here; that only happens if an
upload fails, and it is `app.py` that decides to do it.

A recording can be paused. Pausing releases the microphone entirely rather than merely
ignoring what arrives — so the device's in-use light goes out, which is the only honest
way to show someone that nothing is being captured.
"""

from __future__ import annotations

import io
import logging
import math
import threading
import time
import wave
from dataclasses import dataclass

import sounddevice as sd

logger = logging.getLogger(__name__)

CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2  # int16
INT16_FULL_SCALE = 32768.0

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


def _loudness(pcm: bytes) -> float:
    """Rough 0-1 loudness of one block, for the level meter in the window."""
    if not pcm:
        return 0.0
    total = 0
    count = 0
    for index in range(0, len(pcm) - 1, 2 * 8):  # every 8th sample is plenty for a bar
        sample = int.from_bytes(pcm[index : index + 2], "little", signed=True)
        total += sample * sample
        count += 1
    if not count:
        return 0.0
    return min(1.0, math.sqrt(total / count) / INT16_FULL_SCALE * 4)


class Recorder:
    """Owns the input stream. Exactly one capture may be in flight at a time."""

    def __init__(self, sample_rate: int, device: int | str | None = None) -> None:
        self._sample_rate = sample_rate
        self._device = device
        self._stream: sd.InputStream | None = None
        self._chunks: list[bytes] = []
        self._segment_started_at: float | None = None
        self._recorded_seconds = 0.0  # completed segments, excluding paused time
        self._last_audio_at: float | None = None
        self._paused = False
        self._level = 0.0
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        """True from the first `start` until `stop` or `cancel`, paused or not."""
        return self._stream is not None or self._paused

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def level(self) -> float:
        """How loud the last block was, 0 to 1. Zero while paused."""
        return 0.0 if self._paused else self._level

    @property
    def elapsed_seconds(self) -> float:
        """Seconds of audio captured so far, not counting time spent paused."""
        with self._lock:
            running = (
                0.0
                if self._segment_started_at is None
                else time.monotonic() - self._segment_started_at
            )
            return self._recorded_seconds + running

    def start(self) -> None:
        """Open the microphone and begin collecting samples."""
        with self._lock:
            if self._stream is not None:
                return
            self._chunks = []
            self._recorded_seconds = 0.0
            self._paused = False
        self._open_stream()

    def pause(self) -> None:
        """Release the microphone but keep what has been captured so far."""
        if self._paused or self._stream is None:
            return
        self._release_stream()
        self._paused = True
        self._level = 0.0
        logger.info("recording paused at %.1fs", self.elapsed_seconds)

    def resume(self) -> None:
        """Reopen the microphone and carry on appending to the same take."""
        if not self._paused:
            return
        self._paused = False
        self._open_stream()
        logger.info("recording resumed")

    def stop(self) -> Recording:
        """Close the microphone and return what was captured."""
        self._release_stream()
        pcm = self._drain()
        duration = pcm_duration_seconds(pcm, self._sample_rate)
        logger.info("recording stopped (%.1f s)", duration)
        return Recording(wav_bytes=build_wav(pcm, self._sample_rate), duration_seconds=duration)

    def cancel(self) -> None:
        """Close the microphone and throw the samples away. Nothing is uploaded."""
        self._release_stream()
        self._drain()
        logger.info("recording cancelled by the user")

    # ------------------------------------------------------------------ stream handling

    def _open_stream(self) -> None:
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
            self._paused = False
            raise RecorderError(f"could not open the microphone: {exc}") from exc

        with self._lock:
            self._stream = stream
            self._segment_started_at = time.monotonic()
            self._last_audio_at = self._segment_started_at
        logger.info("recording started (device=%s, %d Hz)", self._device, self._sample_rate)

    def _release_stream(self) -> None:
        """Close the stream and bank the elapsed time. The captured samples stay put.

        `abort` rather than `stop`: stop waits for the device to drain, and on a device
        that has been unplugged PortAudio can wait forever. Every sample has already been
        collected by the callback, so there is nothing to drain.
        """
        with self._lock:
            stream, self._stream = self._stream, None
            if self._segment_started_at is not None:
                self._recorded_seconds += time.monotonic() - self._segment_started_at
                self._segment_started_at = None
            last_audio_at = self._last_audio_at

        if stream is not None:
            try:
                stream.abort(ignore_errors=True)
            finally:
                stream.close(ignore_errors=True)

        if (
            stream is not None
            and last_audio_at is not None
            and time.monotonic() - last_audio_at > STALE_AUDIO_SECONDS
        ):
            # PortAudio has no device-removal notification: when a microphone disappears
            # the callback simply stops being called. Silence in the log is the only clue.
            logger.warning(
                "no audio arrived for over %.1fs — the microphone may have been unplugged",
                STALE_AUDIO_SECONDS,
            )

    def _drain(self) -> bytes:
        with self._lock:
            chunks, self._chunks = self._chunks, []
            self._recorded_seconds = 0.0
            self._paused = False
            self._level = 0.0
        return b"".join(chunks)

    def _on_audio(self, indata, _frames: int, _time_info, status) -> None:
        """PortAudio callback. Runs on the audio thread — keep it short."""
        if status:
            logger.warning("audio input status: %s", status)
        block = bytes(indata)
        self._chunks.append(block)
        self._last_audio_at = time.monotonic()
        self._level = _loudness(block)

    def _on_stream_finished(self) -> None:
        """PortAudio has abandoned the stream — usually the microphone was unplugged.

        Without this, `is_recording` would stay True forever and the hotkey would appear
        to do nothing: every press would be treated as 'already recording'.
        """
        with self._lock:
            if self._stream is None:
                return  # the ordinary path — `_release_stream` already took ownership
            self._stream = None
            if self._segment_started_at is not None:
                self._recorded_seconds += time.monotonic() - self._segment_started_at
                self._segment_started_at = None
        logger.warning("the audio device stopped on its own — the microphone may be gone")
