"""ElevenLabs Scribe v2 client, plus the running cost tally shown in the tray menu.

The language is forced rather than auto-detected: on short Georgian clips, detection is
the single biggest source of nonsense output.
"""

from __future__ import annotations

import io
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from elevenlabs.client import ElevenLabs

logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 1.5
SECONDS_PER_HOUR = 3600


class TranscriptionError(Exception):
    """The audio could not be turned into text. The message is shown to the user."""


@dataclass(frozen=True)
class Usage:
    """Cumulative spend, as recorded on disk."""

    calls: int
    total_seconds: float
    total_cost_usd: float


def _is_retryable(exc: Exception) -> bool:
    """Network trouble and server faults are worth one more try; a rejected key is not."""
    status = getattr(exc, "status_code", None)
    if status is None:
        return True  # no HTTP status at all — a connection problem
    return status >= 500 or status == 429


def _explain(exc: Exception) -> str:
    """Turn an SDK exception into a sentence, without echoing the request back."""
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return "ElevenLabs rejected the API key — check it at elevenlabs.io/app/settings/api-keys"
    if status == 429:
        return "ElevenLabs is rate limiting the account — wait a moment and try again"
    if status is not None and status >= 500:
        return f"ElevenLabs had a server problem (HTTP {status}) — the recording was kept"
    return f"could not reach ElevenLabs: {type(exc).__name__}"


class UsageLog:
    """Cumulative seconds and estimated cost, kept in a small JSON file."""

    def __init__(self, path: Path, price_per_hour_usd: float) -> None:
        self._path = path
        self._price_per_hour = price_per_hour_usd

    def read(self) -> Usage:
        """Return the tally so far, or zeros if nothing has been recorded yet."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return Usage(
                calls=int(raw.get("calls", 0)),
                total_seconds=float(raw.get("total_seconds", 0.0)),
                total_cost_usd=float(raw.get("total_cost_usd", 0.0)),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return Usage(calls=0, total_seconds=0.0, total_cost_usd=0.0)

    def add(self, seconds: float) -> Usage:
        """Record one successful transcription and return the new totals."""
        previous = self.read()
        updated = Usage(
            calls=previous.calls + 1,
            total_seconds=previous.total_seconds + seconds,
            total_cost_usd=previous.total_cost_usd
            + seconds / SECONDS_PER_HOUR * self._price_per_hour,
        )
        payload = {
            "calls": updated.calls,
            "total_seconds": round(updated.total_seconds, 2),
            "total_cost_usd": round(updated.total_cost_usd, 6),
            "price_per_hour_usd": self._price_per_hour,
            "updated": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("could not write the usage file: %s", exc)
        return updated


class Transcriber:
    """Sends WAV audio to Scribe v2 and returns the text."""

    def __init__(self, api_key: str, model_id: str, language_code: str) -> None:
        self._client = ElevenLabs(api_key=api_key)
        self._model_id = model_id
        self._language_code = language_code

    def transcribe(self, wav_bytes: bytes) -> str:
        """Return the transcript. Retries once on a transient failure, then gives up."""
        last_error: Exception | None = None

        for attempt in (1, 2):
            try:
                return self._convert(wav_bytes)
            except TranscriptionError:
                # Already a decided answer — a silent recording will be just as silent
                # on a second attempt, and its message is more useful than a generic one.
                raise
            except Exception as exc:
                last_error = exc
                if attempt == 2 or not _is_retryable(exc):
                    break
                logger.warning("transcription attempt %d failed, retrying: %s", attempt, exc)
                time.sleep(RETRY_DELAY_SECONDS)

        assert last_error is not None
        logger.error("transcription failed: %s", last_error)
        raise TranscriptionError(_explain(last_error)) from last_error

    def _convert(self, wav_bytes: bytes) -> str:
        """One call to the API. The key travels in a header the SDK sets, never in the URL."""
        audio = io.BytesIO(wav_bytes)
        audio.name = "recording.wav"  # the SDK uses this for the multipart filename

        result = self._client.speech_to_text.convert(
            file=audio,
            model_id=self._model_id,
            language_code=self._language_code,
            tag_audio_events=False,  # no "(laughs)" markers pasted into the user's text
            diarize=False,  # one speaker
        )

        text = (getattr(result, "text", "") or "").strip()
        if not text:
            raise TranscriptionError("ElevenLabs returned no text — the recording may be silent")
        return text
