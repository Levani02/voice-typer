"""The state machine that connects the pieces.

IDLE -> RECORDING -> TRANSCRIBING -> IDLE, with ERROR as a display state that falls back
to IDLE. This is the only module aware of more than one other module.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from voice_typer.config import LOGS_DIR, Config
from voice_typer.hotkey import Action, HotkeyListener
from voice_typer.injector import InjectionError, inject_text
from voice_typer.recorder import Recorder, RecorderError, Recording
from voice_typer.transcriber import Transcriber, TranscriptionError, UsageLog
from voice_typer.tray import TrayIcon, TrayState

logger = logging.getLogger(__name__)

LAST_RECORDING_PATH = LOGS_DIR / "last_recording.wav"
USAGE_PATH = LOGS_DIR / "usage.json"
CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

# How long the icon stays dark red before returning to grey. Long enough to notice,
# short enough that the app does not look permanently broken.
ERROR_DISPLAY_SECONDS = 6.0


class App:
    """Owns the recording lifecycle. The tray is attached after construction."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._recorder = Recorder(config.sample_rate, config.input_device)
        self._transcriber = Transcriber(config.api_key, config.model_id, config.language_code)
        self._usage = UsageLog(USAGE_PATH, config.price_per_hour_usd)
        self._hotkey = HotkeyListener(config.hotkey, config.hold_threshold_ms, self._handle_action)
        self._tray: TrayIcon | None = None
        self._auto_stop: threading.Timer | None = None
        self._error_reset: threading.Timer | None = None
        self._busy = threading.Lock()

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

        self._set_state(TrayState.TRANSCRIBING)
        threading.Thread(target=self._transcribe_and_paste, args=(recording,), daemon=True).start()

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

    # ------------------------------------------------------------------- transcription

    def _transcribe_and_paste(self, recording: Recording) -> None:
        """Runs on a worker thread so the keyboard listener is never blocked."""
        with self._busy:
            try:
                text = self._transcriber.transcribe(recording.wav_bytes)
            except TranscriptionError as exc:
                self._keep_for_retry(recording.wav_bytes)
                self._report_error(str(exc), "ტექსტად გარდაქმნა ვერ მოხერხდა")
                return

            self._record_usage(recording.duration_seconds, len(text))
            self._paste(text)

    def _record_usage(self, seconds: float, characters: int) -> None:
        usage = self._usage.add(seconds)
        logger.info(
            "transcribed %.1fs into %d characters (total spent: $%.4f)",
            seconds,
            characters,
            usage.total_cost_usd,
        )

    def _paste(self, text: str) -> None:
        if self._config.log_transcripts:
            logger.debug("transcript: %s", text)

        try:
            inject_text(
                text,
                restore_clipboard=self._config.restore_clipboard,
                restore_delay_ms=self._config.clipboard_restore_delay_ms,
            )
        except InjectionError as exc:
            self._report_error(str(exc), "ტექსტი clipboard-შია — დააჭირე Ctrl+V")
            return

        self._set_state(TrayState.IDLE)

    def _keep_for_retry(self, wav_bytes: bytes) -> None:
        """Never lose the user's words to a network problem."""
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            LAST_RECORDING_PATH.write_bytes(wav_bytes)
            logger.info("kept the recording for retry at %s", LAST_RECORDING_PATH)
        except OSError as exc:
            logger.warning("could not keep the recording for retry: %s", exc)

    # -------------------------------------------------------------------- tray actions

    def retry_last(self) -> None:
        """Re-send the recording kept after a failed upload."""
        if not LAST_RECORDING_PATH.exists():
            self._notify("შესანახი ჩანაწერი არაა")
            return

        wav_bytes = LAST_RECORDING_PATH.read_bytes()
        from voice_typer.recorder import pcm_duration_seconds  # local: only needed here

        duration = pcm_duration_seconds(wav_bytes, self._config.sample_rate)
        self._set_state(TrayState.TRANSCRIBING)
        recording = Recording(wav_bytes=wav_bytes, duration_seconds=duration)
        threading.Thread(target=self._transcribe_and_paste, args=(recording,), daemon=True).start()

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
        self._error_reset = None
        if not self._recorder.is_recording:
            self._set_state(TrayState.IDLE)
