"""One copy of the app at a time.

Two copies would both listen for F9, both record the same words, both pay for the
transcription, and both paste — so the user would get everything twice and be billed
twice. Nothing in the app prevented that until now.

Both systems use a lock the kernel owns, so a crash cannot leave a stale one behind the
way a plain marker file would:

* **Windows** — a named mutex, released when the process ends, killed or not.
* **macOS and Linux** — an exclusive `flock` on a file under `logs/`. The file survives,
  which is harmless; the lock on it does not.
"""

from __future__ import annotations

import logging
from pathlib import Path

from voice_typer.config import LOGS_DIR
from voice_typer.platform_support import IS_WINDOWS

logger = logging.getLogger(__name__)

MUTEX_NAME = "Local\\voice-typer-single-instance"
LOCK_PATH = LOGS_DIR / "voice-typer.lock"
ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Holds the lock for as long as this process lives."""

    def __init__(self, name: str = MUTEX_NAME, lock_path: Path = LOCK_PATH) -> None:
        self._name = name
        self._lock_path = lock_path
        self._handle: int | None = None
        self._lock_file = None

    def acquire(self) -> bool:
        """True if this is the only copy. False if another one is already running."""
        try:
            return self._acquire_mutex() if IS_WINDOWS else self._acquire_flock()
        except Exception as exc:
            # A missing lock is far less bad than refusing to start at all.
            logger.warning("single-instance check failed, carrying on: %s", exc)
            return True

    def release(self) -> None:
        if IS_WINDOWS:
            self._release_mutex()
        else:
            self._release_flock()

    # ------------------------------------------------------------------------- Windows

    def _acquire_mutex(self) -> bool:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, self._name)
        if not handle:
            logger.warning("could not create the single-instance lock — carrying on")
            return True

        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            logger.info("another copy is already running")
            return False

        self._handle = handle
        return True

    def _release_mutex(self) -> None:
        if self._handle is None:
            return
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._handle)
        except Exception as exc:
            logger.warning("could not release the single-instance lock: %s", exc)
        finally:
            self._handle = None

    # --------------------------------------------------------------------- macOS, Linux

    def _acquire_flock(self) -> bool:
        import fcntl

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Kept open deliberately: closing the file drops the lock with it.
        handle = self._lock_path.open("w", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            logger.info("another copy is already running")
            return False

        self._lock_file = handle
        return True

    def _release_flock(self) -> None:
        if self._lock_file is None:
            return
        try:
            import fcntl

            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()
        except Exception as exc:
            logger.warning("could not release the single-instance lock: %s", exc)
        finally:
            self._lock_file = None
