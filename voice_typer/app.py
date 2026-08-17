"""The state machine that connects the pieces.

IDLE -> RECORDING -> TRANSCRIBING -> IDLE, with ERROR as a display state that falls back
to IDLE. This is the only module aware of more than one other module.

Two rules hold everywhere below, because breaking either loses the user's words:

* A recording is written to disk *before* it is uploaded and deleted only once its text
  has actually landed in a window. Anything that kills the worker in between — a crash,
  Quit, the power going out — leaves a file the tray menu can re-send.
* Transcription jobs are serialised, and only one may be queued behind the running one.
  The tray always shows the job that is genuinely in flight, never a stale one.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from voice_typer.config import LOGS_DIR, Config
from voice_typer.hotkey import Action, HotkeyListener
from voice_typer.injector import ClipboardUnavailableError, PasteFailedError, inject_text
from voice_typer.recorder import Recorder, RecorderError, Recording, pcm_duration_seconds
from voice_typer.transcriber import Transcriber, TranscriptionError, UsageLog
from voice_typer.tray import TrayIcon, TrayState

logger = logging.getLogger(__name__)

LAST_RECORDING_PATH = LOGS_DIR / "last_recording.wav"
LAST_TRANSCRIPT_PATH = LOGS_DIR / "last_transcript.txt"
USAGE_PATH = LOGS_DIR / "usage.json"
CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

# How long the icon stays dark red before returning to grey. Long enough to notice,
# short enough that the app does not look permanently broken.
ERROR_DISPLAY_SECONDS = 6.0

# How long Quit waits for a transcription that is already in flight. The recording is on
# disk either way, so this is about finishing gracefully, not about safety.
SHUTDOWN_WAIT_SECONDS = 15.0


class App:
    """Owns the recording lifecycle. The tray is attached after construction."""

    def __init__(
        self,
        config: Config,
        *,
        recorder: Recorder | None = None,
        transcriber: Transcriber | None = None,
        usage: UsageLog | None = None,
        hotkey: HotkeyListener | None = None,
    ) -> None:
        # The collaborators are injectable so the state machine can be tested without a
        # microphone, a keyboard hook, or a network connection.
        self._config = config
        self._recorder = recorder or Recorder(config.sample_rate, config.input_device)
        self._transcriber = transcriber or Transcriber(
            config.api_key, config.model_id, config.language_code
        )
        self._usage = usage or UsageLog(USAGE_PATH, config.price_per_hour_usd)
        self._hotkey = hotkey or HotkeyListener(
            config.hotkey, config.hold_threshold_ms, self._handle_action
        )
        self._tray: TrayIcon | None = None
        self._auto_stop: threading.Timer | None = None
        self._error_reset: threading.Timer | None = None
        self._transcribing = threading.Lock()  # serialises the workers themselves
        self._jobs = threading.Condition()  # guards the count below
        self._pending_jobs = 0

    def attach_tray(self, tray: TrayIcon) -> None:
        self._tray = tray

    def start(self) -> None:
        self._hotkey.start()
        logger.info("ready — press %s to dictate", self._config.hotkey)

    def shutdown(self) -> None:
        """Release the keyboard hook, the timers, and the microphone, in that order."""
        self._cancel_auto_stop()
        self._cancel_error_reset()
        self._hotkey.stop()
        if self._recorder.is_recording:
            self._recorder.cancel()

        if self.is_busy:
            self._notify("ბოლო ჩანაწერი მუშავდება — ერთი წამი")
            if not self._wait_for_jobs(SHUTDOWN_WAIT_SECONDS):
                logger.warning(
                    "quit while a transcription was still running — the recording is kept at %s",
                    LAST_RECORDING_PATH,
                )
        logger.info("shut down")

    # ------------------------------------------------------------------ hotkey handling

    def _handle_action(self, action: Action) -> None:
        if action is Action.START:
            self._start_recording()
        elif action is Action.STOP:
            self._stop_recording()
        elif action is Action.CANCEL:
            self._cancel_recording()

    def _start_recording(self) -> None:
        self._cancel_error_reset()
        try:
            self._recorder.start()
        except RecorderError as exc:
            self._hotkey.logic.force_idle()
            self._report_error(str(exc), "მიკროფონი ვერ ჩაირთო")
            return

        self._set_state(TrayState.RECORDING)
        self._schedule_auto_stop()

    def _stop_recording(self) -> None:
        self._cancel_auto_stop()
        if not self._recorder.is_recording:
            return

        recording = self._recorder.stop()
        if recording.duration_seconds * 1000 < self._config.min_recording_ms:
            logger.info(
                "discarded a %.2fs clip — too short to be speech", recording.duration_seconds
            )
            self._set_state(TrayState.IDLE)
            return

        self._spawn_job(recording)

    def _cancel_recording(self) -> None:
        self._cancel_auto_stop()
        if self._recorder.is_recording:
            self._recorder.cancel()
        self._set_state(TrayState.IDLE)

    # ---------------------------------------------------------------------- auto-stop

    def _schedule_auto_stop(self) -> None:
        """A stuck key must not be able to record — and bill — indefinitely."""
        self._cancel_auto_stop()
        timer = threading.Timer(self._config.max_recording_seconds, self._on_auto_stop)
        timer.daemon = True
        timer.start()
        self._auto_stop = timer

    def _cancel_auto_stop(self) -> None:
        if self._auto_stop is not None:
            self._auto_stop.cancel()
            self._auto_stop = None

    def _on_auto_stop(self) -> None:
        logger.warning("hit the %ds ceiling — stopping", self._config.max_recording_seconds)
        self._hotkey.logic.force_idle()
        self._notify(f"ჩაწერა შეჩერდა {self._config.max_recording_seconds} წამის შემდეგ")
        self._stop_recording()

    # ------------------------------------------------------------------- job accounting

    @property
    def is_busy(self) -> bool:
        """True while any transcription is running or queued."""
        with self._jobs:
            return self._pending_jobs > 0

    def _wait_for_jobs(self, timeout_seconds: float) -> bool:
        """Block until no job remains. False if the timeout ran out first."""
        with self._jobs:
            return self._jobs.wait_for(lambda: self._pending_jobs == 0, timeout=timeout_seconds)

    def _job_finished(self) -> None:
        with self._jobs:
            self._pending_jobs -= 1
            self._jobs.notify_all()

    def _spawn_job(self, recording: Recording) -> None:
        """Save the audio first, then hand it to a worker thread."""
        self._keep_for_retry(recording.wav_bytes)
        with self._jobs:
            self._pending_jobs += 1
        self._set_state(TrayState.TRANSCRIBING)
        threading.Thread(target=self._transcribe_and_paste, args=(recording,), daemon=True).start()

    # ------------------------------------------------------------------- transcription

    def _transcribe_and_paste(self, recording: Recording) -> None:
        """Runs on a worker thread so the keyboard listener is never blocked."""
        try:
            with self._transcribing:
                # Re-assert the state here, not at spawn time: a job queued behind another
                # only becomes the one the icon is describing once it actually starts.
                self._set_state(TrayState.TRANSCRIBING)
                self._run_job(recording)
        finally:
            self._job_finished()

    def _run_job(self, recording: Recording) -> None:
        try:
            text = self._transcriber.transcribe(recording.wav_bytes)
        except TranscriptionError as exc:
            self._report_error(str(exc), "ტექსტად გარდაქმნა ვერ მოხერხდა — ჩანაწერი შენახულია")
            return

        self._record_usage(recording.duration_seconds, len(text))
        if self._paste(text):
            self._discard_kept_recording()

    def _record_usage(self, seconds: float, characters: int) -> None:
        usage = self._usage.add(seconds)
        logger.info(
            "transcribed %.1fs into %d characters (total spent: $%.4f)",
            seconds,
            characters,
            usage.total_cost_usd,
        )

    def _paste(self, text: str) -> bool:
        """True once the text is in the window. False leaves the recording on disk."""
        if self._config.log_transcripts:
            logger.debug("transcript: %s", text)

        try:
            inject_text(
                text,
                restore_clipboard=self._config.restore_clipboard,
                restore_delay_ms=self._config.clipboard_restore_delay_ms,
            )
        except PasteFailedError as exc:
            self._report_error(str(exc), "ტექსტი clipboard-შია — დააჭირე Ctrl+V")
            return False
        except ClipboardUnavailableError as exc:
            self._save_transcript(text)
            self._report_error(str(exc), "ჩასმა ვერ მოხერხდა — ტექსტი logs საქაღალდეშია")
            return False

        self._set_state(TrayState.IDLE)
        return True

    # ------------------------------------------------------------------------- salvage

    def _keep_for_retry(self, wav_bytes: bytes) -> None:
        """Written before every upload, so nothing that kills the worker loses the words."""
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            LAST_RECORDING_PATH.write_bytes(wav_bytes)
        except OSError as exc:
            logger.warning("could not keep the recording for retry: %s", exc)

    def _discard_kept_recording(self) -> None:
        """The words made it into a window — the copy on disk is no longer needed."""
        try:
            LAST_RECORDING_PATH.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not remove the kept recording: %s", exc)

    def _save_transcript(self, text: str) -> None:
        """Last resort when the clipboard itself is unusable and Ctrl+V would find nothing."""
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            LAST_TRANSCRIPT_PATH.write_text(text, encoding="utf-8")
            logger.info("clipboard unusable — wrote the text to %s", LAST_TRANSCRIPT_PATH)
        except OSError as exc:
            logger.error("could not save the transcript anywhere: %s", exc)

    # -------------------------------------------------------------------- tray actions

    def retry_last(self) -> None:
        """Re-send the recording kept from the last job that did not finish."""
        if self.is_busy:
            self._notify("ჯერ წინა ჩანაწერი მუშავდება")
            return
        if not LAST_RECORDING_PATH.exists():
            self._notify("ხელახლა გასაგზავნი ჩანაწერი არაა")
            return

        try:
            wav_bytes = LAST_RECORDING_PATH.read_bytes()
        except OSError as exc:
            self._report_error(str(exc), "შენახული ჩანაწერი ვერ წაიკითხა")
            return

        duration = pcm_duration_seconds(wav_bytes, self._config.sample_rate)
        self._spawn_job(Recording(wav_bytes=wav_bytes, duration_seconds=duration))

    def usage_text(self) -> str:
        usage = self._usage.read()
        return f"ხარჯი: ${usage.total_cost_usd:.4f} · {usage.total_seconds / 60:.1f} წუთი"

    def open_logs(self) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(LOGS_DIR)  # a fixed path, never taken from config

    def open_settings(self) -> None:
        os.startfile(CONFIG_FILE)  # a fixed path, never taken from config

    # ------------------------------------------------------------------------- helpers

    def _set_state(self, state: TrayState) -> None:
        if self._tray is not None:
            self._tray.set_state(state)

    def _notify(self, message: str) -> None:
        if self._tray is not None:
            self._tray.notify(message)

    def _report_error(self, detail: str, user_message: str) -> None:
        """Detail goes to the log; the user gets a sentence they can act on."""
        logger.error("%s: %s", user_message, detail)
        self._set_state(TrayState.ERROR)
        self._notify(user_message)
        self._clear_error_after(ERROR_DISPLAY_SECONDS)

    def _clear_error_after(self, seconds: float) -> None:
        """Return the icon to grey, but only if nothing has started in the meantime."""
        self._cancel_error_reset()
        timer = threading.Timer(seconds, self._reset_to_idle)
        timer.daemon = True
        timer.start()
        self._error_reset = timer

    def _cancel_error_reset(self) -> None:
        if self._error_reset is not None:
            self._error_reset.cancel()
            self._error_reset = None

    def _reset_to_idle(self) -> None:
        """Never overwrite the state of work that is still going on."""
        self._error_reset = None
        if not self._recorder.is_recording and not self.is_busy:
            self._set_state(TrayState.IDLE)
