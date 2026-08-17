"""One copy of the app at a time.

Two copies would both listen for F9, both record the same words, both pay for the
transcription, and both paste — so the user would get everything twice and be billed
twice. Nothing in the app prevented that until now.

A named Windows mutex is the right tool: the kernel releases it when the process ends,
including when the process is killed, so a crash cannot leave a stale lock behind the way
a lock file would.
"""

from __future__ import annotations

import ctypes
import logging

logger = logging.getLogger(__name__)

MUTEX_NAME = "Local\\voice-typer-single-instance"
ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Holds the mutex for as long as this process lives."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._name = name
        self._handle: int | None = None

    def acquire(self) -> bool:
        """True if this is the only copy. False if another one is already running."""
        try:
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
        except Exception as exc:
            # A missing lock is far less bad than refusing to start at all.
            logger.warning("single-instance check failed, carrying on: %s", exc)
            return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            ctypes.windll.kernel32.CloseHandle(self._handle)
        except Exception as exc:
            logger.warning("could not release the single-instance lock: %s", exc)
        finally:
            self._handle = None
