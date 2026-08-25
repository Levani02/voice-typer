"""The recordings kept on disk between speaking and seeing the words appear.

Each take is written to `logs/pending/take-NNNN.wav` just before its upload starts and
deleted the moment its own text lands in a window. A crash or a lost connection therefore
leaves something to re-send; a dictation that worked leaves nothing behind.

Split out of `app.py` because none of it needs the state machine: every function here
takes the paths it works on as arguments. That is deliberate rather than tidy — `app.py`
keeps the module-level paths so a test can point them at a temporary folder, and any
default value or cached copy here would quietly aim the suite at the user's real `logs/`
folder instead.

Every function swallows its own `OSError`. Failing to tidy up an old recording is not a
reason to stop a dictation, and failing to keep one is worth a line in the log rather
than an exception on the worker thread.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PENDING_PATTERN = "take-*.wav"
PENDING_NUMBER = re.compile(r"take-(\d+)\.wav$")

SECONDS_PER_DAY = 86_400


def path_for(directory: Path, number: int) -> Path:
    """Where take `number` lives. One take, one file — never two sharing a name, or the
    later one would overwrite the earlier one's safety net."""
    return directory / f"take-{number:04d}.wav"


def highest_number(directory: Path) -> int:
    """Continue numbering above whatever a previous session left behind."""
    try:
        names = [p.name for p in directory.glob(PENDING_PATTERN)]
    except OSError:
        return 0
    numbers = [int(m.group(1)) for name in names if (m := PENDING_NUMBER.search(name))]
    return max(numbers, default=0)


def orphans(directory: Path) -> list[Path]:
    """Takes a previous session left behind, oldest first.

    Only the listing. Choosing one and claiming it has to happen under the app's lock,
    or two Retry clicks could pick the same file and paste the same words twice.
    """
    try:
        return sorted(directory.glob(PENDING_PATTERN))
    except OSError:
        return []


def keep_for_retry(wav_bytes: bytes, path: Path) -> None:
    """Written before the upload, so anything that kills the worker leaves the words."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav_bytes)
    except OSError as exc:
        logger.warning("could not keep the recording for retry: %s", exc)


def discard(path: Path) -> None:
    """This take's words made it into a window — its copy on disk is no longer needed."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("could not remove the kept recording: %s", exc)


def prune_old(directory: Path, older_than_days: int) -> None:
    """Delete recordings past the configured age, so they do not pile up."""
    cutoff = time.time() - older_than_days * SECONDS_PER_DAY
    try:
        stale = [p for p in directory.glob(PENDING_PATTERN) if p.stat().st_mtime < cutoff]
    except OSError:
        return
    for path in stale:
        try:
            path.unlink(missing_ok=True)
            logger.info("removed a recording older than %d days", older_than_days)
        except OSError as exc:
            logger.warning("could not remove an old recording: %s", exc)


def save_transcript(text: str, logs_dir: Path, path: Path) -> None:
    """Last resort when the clipboard itself is unusable and Ctrl+V would find nothing.

    The one place a transcript is deliberately written to disk. Losing what somebody just
    said is the worse outcome, which is why this exception to "transcripts are not logged"
    exists at all — and why it must not be extended to any other path quietly.
    """
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        logger.info("clipboard unusable — wrote the text to %s", path)
    except OSError as exc:
        logger.error("could not save the transcript anywhere: %s", exc)
