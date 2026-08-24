"""The state machine that connects the pieces.

IDLE -> RECORDING -> TRANSCRIBING -> IDLE, with ERROR as a display state that falls back
to whatever is actually happening. This is the only module aware of more than one other
module.

Three rules hold everywhere below, because breaking any of them loses the user's words:

* **Every take gets its own file.** A recording is written to `logs/pending/` before it is
  uploaded and deleted only once its own text has landed in a window. Starting a second
  dictation while the first is still uploading is ordinary use, so one shared file would
  not do — the second take would overwrite the first take's safety net.
* **Nothing slow runs on the keyboard hook thread.** `_stop_recording` is called from
  inside a global low-level Windows keyboard hook; every keystroke on the machine waits
  behind it. Disk writes and network calls belong on the worker thread.
* **The tray shows what is true right now**, computed from the recorder and the job count
  rather than set blindly by whichever thread finished last.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

from voice_typer.config import CONFIG_PATH, LOGS_DIR, Config
from voice_typer.focus import foreground_window, is_our_window
from voice_typer.hotkey import Action, HotkeyListener
from voice_typer.injector import (
    PASTE_SHORTCUT_LABEL,
    ClipboardUnavailableError,
    PasteFailedError,
    inject_text,
)
from voice_typer.platform_support import open_path
from voice_typer.recorder import Recorder, RecorderError, Recording, wav_duration_seconds
from voice_typer.transcriber import (
    NothingToPasteError,
    Transcriber,
    Transcript,
    TranscriptionError,
    UsageLog,
)
from voice_typer.tray import TrayIcon, TrayState

logger = logging.getLogger(__name__)

PENDING_DIR = LOGS_DIR / "pending"
PENDING_PATTERN = "take-*.wav"
PENDING_NUMBER = re.compile(r"take-(\d+)\.wav$")
LAST_TRANSCRIPT_PATH = LOGS_DIR / "last_transcript.txt"
USAGE_PATH = LOGS_DIR / "usage.json"

# How long the icon stays dark red before returning to grey. Long enough to notice,
# short enough that the app does not look permanently broken.
ERROR_DISPLAY_SECONDS = 6.0

# How long Quit waits for a transcription already in flight. Kept short: the audio is on
# disk either way, so this is only about finishing gracefully — and the wait happens on
# the thread that draws the tray icon, which would otherwise look frozen.
SHUTDOWN_WAIT_SECONDS = 5.0

SECONDS_PER_DAY = 86_400

# How often to note which window the user is working in. One Win32 call, so the cost is
# nil; often enough that clicking this app's own button cannot outrun it.
FOCUS_POLL_SECONDS = 0.2

# The window and the tray must never disagree about what is happening, so both are drawn
# from the single word `ui_state` returns.
_TRAY_STATE_FOR = {
    "idle": TrayState.IDLE,
    "recording": TrayState.RECORDING,
    "paused": TrayState.PAUSED,
    "transcribing": TrayState.TRANSCRIBING,
    "error": TrayState.ERROR,
    "disabled": TrayState.DISABLED,
}


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
            config.api_key,
            config.model_id,
            config.language_code,
            config.keyterms,
            no_verbatim=config.no_verbatim,
            filler_words=config.filler_words,
        )
        self._usage = usage or UsageLog(USAGE_PATH, config.price_per_hour_usd)
        self._hotkey = hotkey or HotkeyListener(
            config.hotkey, config.hold_threshold_ms, self._handle_action
        )
        self._tray: TrayIcon | None = None
        self._auto_stop: threading.Timer | None = None
        self._error_reset: threading.Timer | None = None
        self._transcribing = threading.Lock()  # serialises the uploads themselves
        self._jobs = threading.Condition()  # guards everything below
        self._pending_jobs = 0
        self._claimed: set[Path] = set()  # files a live job is responsible for
        self._take_counter = 0
        self._has_shut_down = False
        self._enabled = True  # the window's menu can switch hotkey listening off
        self._showing_error = False
        self._quit_handler: Callable[[], None] | None = None
        self._target_window = 0  # where the user was typing before touching this app
        self._started_in_our_window = False  # was this take begun by clicking our button?
        self._watching_focus = threading.Event()
        self._device_label: str | None = None  # looked up once, on first use
        # False means the words go where the cursor is, as they were spoken. True puts the
        # instruction from config.json in front of them, so whatever receives the paste —
        # a chat box with an assistant in it — does the summarising. This app asks no
        # model anything either way.
        self._rewrite_mode = False

    def attach_tray(self, tray: TrayIcon) -> None:
        self._tray = tray

    def start(self) -> None:
        self._prune_old_takes()
        self._take_counter = _highest_take_number(PENDING_DIR)
        self._start_watching_focus()
        self._hotkey.start()
        logger.info("ready — press %s to dictate", self._config.hotkey)

    def _start_watching_focus(self) -> None:
        """Keep track of the window the user is actually working in.

        It cannot be read when a recording starts: by then the user may have clicked this
        app's own record button, which already moved the focus here. So the last window
        that was not ours is remembered continuously — one cheap call every fifth of a
        second — and that is where the transcript goes.
        """

        def watch() -> None:
            while not self._watching_focus.wait(FOCUS_POLL_SECONDS):
                handle = foreground_window()
                if handle and not is_our_window(handle):
                    self._target_window = handle

        threading.Thread(target=watch, name="focus-watch", daemon=True).start()

    def shutdown(self) -> None:
        """Release the keyboard hook, the timers, and the microphone, in that order."""
        with self._jobs:
            if self._has_shut_down:
                return  # called from both the tray menu and main()'s finally block
            self._has_shut_down = True

        self._cancel_auto_stop()
        self._cancel_error_reset()
        self._watching_focus.set()
        self._hotkey.stop()
        if self._recorder.is_recording:
            self._recorder.cancel()

        if self.is_busy:
            self._notify("ბოლო ჩანაწერი მუშავდება — ერთი წამი")
            if not self._wait_for_jobs(SHUTDOWN_WAIT_SECONDS):
                logger.warning("quit with a transcription still running — its audio is kept")
        logger.info("shut down")

    # ------------------------------------------------------------------ hotkey handling

    def _handle_action(self, action: Action) -> None:
        if action is Action.START:
            self._start_recording()
        elif action is Action.STOP:
            self._stop_recording()
        elif action is Action.CANCEL:
            self._cancel_recording()

    def _start_recording(self, *, from_window: bool = False) -> None:
        # Clicking our own record button takes the focus off whatever the user was typing
        # in; pressing the hotkey never does. On macOS that difference decides whether a
        # paste is safe when the system cannot be asked where the focus went.
        self._started_in_our_window = from_window
        self._cancel_error_reset()
        self._showing_error = False
        try:
            self._recorder.start()
        except RecorderError as exc:
            self._hotkey.logic.force_idle()
            self._report_error(str(exc), "მიკროფონი ვერ ჩაირთო")
            return

        # The device list is rebuilt at the start of every take now, so the name in the
        # footer can go out of date the moment headphones connect. Ask for it again.
        self._device_label = None

        self._settle_state()
        self._schedule_auto_stop()

    def _stop_recording(self) -> None:
        """Runs on the keyboard hook thread — hand off quickly and do nothing slow here."""
        self._cancel_auto_stop()
        if not self._recorder.is_recording:
            return

        recording = self._recorder.stop()
        if recording.duration_seconds * 1000 < self._config.min_recording_ms:
            logger.info(
                "discarded a %.2fs clip — too short to be speech", recording.duration_seconds
            )
            self._settle_state()
            return

        self._spawn_job(recording)

    def _cancel_recording(self) -> None:
        self._cancel_auto_stop()
        if self._recorder.is_recording:
            self._recorder.cancel()
        self._settle_state()

    # -------------------------------------------------------------- what the window uses

    def set_quit_handler(self, handler: Callable[[], None]) -> None:
        """Called when the window's power-off or close is used. Set by main()."""
        self._quit_handler = handler

    def ui_state(self) -> str:
        """One word for what is happening, for the window and the tray to agree on."""
        if not self._enabled:
            return "disabled"
        if self._recorder.is_paused:
            return "paused"
        if self._recorder.is_recording:
            return "recording"
        if self.is_busy:
            return "transcribing"
        if self._showing_error:
            return "error"
        return "idle"

    def ui_elapsed_seconds(self) -> float:
        return self._recorder.elapsed_seconds

    def ui_level(self) -> float:
        return self._recorder.level

    def ui_hotkey_label(self) -> str:
        return self._config.hotkey.upper()

    def ui_device_label(self) -> str:
        """Which microphone is in use, for the line along the bottom of the window."""
        if self._device_label is None:
            self._device_label = _describe_input_device(self._config.input_device)
        return self._device_label

    def ui_rewrite_mode(self) -> bool:
        """True when the instruction rides in front of the words."""
        return self._rewrite_mode

    def set_rewrite_mode(self, on: bool) -> None:
        """Used once at startup, to restore the mode the window remembered."""
        self._rewrite_mode = bool(on)

    def toggle_rewrite_mode(self) -> None:
        """The window's mode switch."""
        self._rewrite_mode = not self._rewrite_mode
        logger.info("rewrite mode %s", "on" if self._rewrite_mode else "off")

    def toggle_recording(self) -> None:
        """The window's record button. Does what pressing the hotkey would do."""
        if not self._enabled:
            return
        if self._recorder.is_recording:
            self._hotkey.logic.force_idle()
            self._stop_recording()
        else:
            self._hotkey.logic.force_recording()
            self._start_recording(from_window=True)

    def toggle_pause(self) -> None:
        """Suspend capture without losing what has been said so far, or carry on."""
        if not self._recorder.is_recording:
            return
        try:
            if self._recorder.is_paused:
                self._recorder.resume()
                self._schedule_auto_stop()
            else:
                self._cancel_auto_stop()
                self._recorder.pause()
        except RecorderError as exc:
            self._hotkey.logic.force_idle()
            self._report_error(str(exc), "მიკროფონი ვერ ჩაირთო")
            return
        self._settle_state()

    def cancel_recording(self) -> None:
        """The window's ✕ button — throw the take away, spend nothing."""
        self._hotkey.logic.force_idle()
        self._cancel_recording()

    def toggle_enabled(self) -> None:
        """The window's "F9-ის მოსმენა" menu item: stop listening entirely, or resume."""
        if self._enabled:
            self._enabled = False
            self._cancel_recording()
            self._hotkey.stop()
            logger.info("hotkey listening switched off from the window")
        else:
            self._enabled = True
            self._hotkey.start()
            logger.info("hotkey listening switched back on")
        self._settle_state()

    def quit(self) -> None:
        if self._quit_handler is not None:
            self._quit_handler()

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

    def _spawn_job(self, recording: Recording) -> None:
        """Claim a fresh file for this take and hand it to a worker thread.

        The audio is not written here — that happens on the worker, because this method
        may be running inside the Windows keyboard hook.
        """
        with self._jobs:
            self._take_counter += 1
            path = PENDING_DIR / f"take-{self._take_counter:04d}.wav"
            self._claimed.add(path)
            self._pending_jobs += 1
        self._start_worker(recording, path)

    def _start_worker(self, recording: Recording, path: Path) -> None:
        """Begin work on a take that has already been claimed."""
        self._settle_state()
        worker = threading.Thread(
            target=self._transcribe_and_paste, args=(recording, path), daemon=True
        )
        worker.start()

    def _claim_orphan(self) -> Path | None:
        """Claim the newest recording no job owns — selection and claim in one step.

        Doing these separately let two Retry clicks pick the same file and paste the same
        words twice.
        """
        with self._jobs:
            try:
                found = sorted(PENDING_DIR.glob(PENDING_PATTERN))
            except OSError:
                return None
            orphans = [p for p in found if p not in self._claimed]
            if not orphans:
                return None

            path = orphans[-1]
            self._claimed.add(path)
            self._pending_jobs += 1
            return path

    def _finish_job(self, path: Path) -> None:
        with self._jobs:
            self._pending_jobs -= 1
            self._claimed.discard(path)
            self._jobs.notify_all()

    def _prune_old_takes(self) -> None:
        """Delete recordings older than the configured age, so they do not pile up."""
        cutoff = time.time() - self._config.prune_takes_after_days * SECONDS_PER_DAY
        try:
            stale = [p for p in PENDING_DIR.glob(PENDING_PATTERN) if p.stat().st_mtime < cutoff]
        except OSError:
            return
        for path in stale:
            try:
                path.unlink(missing_ok=True)
                logger.info(
                    "removed a recording older than %d days", self._config.prune_takes_after_days
                )
            except OSError as exc:
                logger.warning("could not remove an old recording: %s", exc)

    # ------------------------------------------------------------------- transcription

    def _transcribe_and_paste(self, recording: Recording, path: Path) -> None:
        """Runs on a worker thread so the keyboard listener is never blocked."""
        succeeded = False
        try:
            self._keep_for_retry(recording.wav_bytes, path)
            with self._transcribing:
                # Recompute the state here rather than at spawn time: a job queued behind
                # another only becomes the one the icon is describing once it starts.
                self._settle_state()
                succeeded = self._run_job(recording, path)
        except Exception as exc:
            # Nothing may escape a worker thread. An unhandled error here used to leave the
            # icon amber for good, with the user waiting for words that were never coming.
            logger.exception("unexpected failure while transcribing")
            self._report_error(repr(exc), "მოულოდნელი შეცდომა — ჩანაწერი შენახულია")
        finally:
            # Settle only after the count has dropped, or this job would still see itself
            # as pending and leave the icon amber. On failure the error colour stands, and
            # its own timer clears it.
            self._finish_job(path)
            if succeeded:
                self._settle_state()

    def _run_job(self, recording: Recording, path: Path) -> bool:
        try:
            transcript = self._transcriber.transcribe(recording.wav_bytes)
        except NothingToPasteError as exc:
            # Not a failure worth the word "error", but it must still land somewhere the
            # user can see, or a take that produced nothing looks like a take that hung.
            self._report_error(str(exc), "მხოლოდ ჩაფიქრების ხმა იყო — ჩასასმელი არაფერია")
            return False
        except TranscriptionError as exc:
            self._report_error(str(exc), "ტექსტად გარდაქმნა ვერ მოხერხდა — ჩანაწერი შენახულია")
            return False

        self._record_usage(transcript, recording.duration_seconds)
        if not self._paste(self._compose(transcript.text)):
            return False

        self._discard(path)
        return True

    def _compose(self, text: str) -> str:
        """What actually goes on the clipboard.

        In rewrite mode the instruction rides in front of the words. Nothing is sent
        anywhere and nothing is rewritten here — the text is simply addressed to whatever
        is on the other side of the paste.
        """
        if not self._rewrite_mode or not self._config.rewrite_instruction:
            return text
        return f"{self._config.rewrite_instruction}\n\n{text}"

    def _record_usage(self, transcript: Transcript, measured_seconds: float) -> None:
        """Prefer the duration ElevenLabs billed for — that is what the invoice will say."""
        seconds = (
            transcript.billed_seconds if transcript.billed_seconds is not None else measured_seconds
        )
        usage = self._usage.add(seconds)
        logger.info(
            "transcribed %.1fs into %d characters (total spent: $%.4f)",
            seconds,
            len(transcript.text),
            usage.total_cost_usd,
        )
        if self._tray is not None:
            self._tray.refresh_menu()  # otherwise the cost line stays at whatever it was

    def _paste(self, text: str) -> bool:
        """True once the text is in the window. False leaves the recording on disk."""
        if self._config.log_transcripts:
            logger.debug("transcript: %s", text)

        try:
            inject_text(
                text,
                restore_clipboard=self._config.restore_clipboard,
                restore_delay_ms=self._config.clipboard_restore_delay_ms,
                target_window=self._target_window,
                started_from_our_window=self._started_in_our_window,
            )
        except PasteFailedError as exc:
            self._report_error(str(exc), f"ტექსტი clipboard-შია — დააჭირე {PASTE_SHORTCUT_LABEL}")
            return False
        except ClipboardUnavailableError as exc:
            self._save_transcript(text)
            self._report_error(str(exc), "ჩასმა ვერ მოხერხდა — ტექსტი logs საქაღალდეშია")
            return False

        return True

    # ------------------------------------------------------------------------- salvage

    def _keep_for_retry(self, wav_bytes: bytes, path: Path) -> None:
        """Written before the upload, so anything that kills the worker leaves the words."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(wav_bytes)
        except OSError as exc:
            logger.warning("could not keep the recording for retry: %s", exc)

    def _discard(self, path: Path) -> None:
        """This take's words made it into a window — its copy on disk is no longer needed."""
        try:
            path.unlink(missing_ok=True)
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
        """Re-send the newest recording that no live job already owns."""
        path = self._claim_orphan()
        if path is None:
            self._notify("ხელახლა გასაგზავნი ჩანაწერი არაა")
            return

        try:
            wav_bytes = path.read_bytes()
            duration = wav_duration_seconds(wav_bytes)
        except (OSError, RecorderError) as exc:
            self._finish_job(path)  # release the claim we just took
            self._report_error(str(exc), "შენახული ჩანაწერი ვერ წაიკითხა")
            return

        self._start_worker(Recording(wav_bytes=wav_bytes, duration_seconds=duration), path)

    def usage_text(self) -> str:
        usage = self._usage.read()
        return f"ხარჯი: ${usage.total_cost_usd:.4f} · {usage.total_seconds / 60:.1f} წუთი"

    def open_logs(self) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        open_path(LOGS_DIR)  # a fixed path, never taken from config

    def open_settings(self) -> None:
        open_path(CONFIG_PATH)  # a fixed path, never taken from config

    # ------------------------------------------------------------------------- helpers

    def _set_state(self, state: TrayState) -> None:
        if self._tray is not None:
            self._tray.set_state(state)

    def _settle_state(self) -> None:
        """Show what is happening now, not what the calling thread happened to finish.

        Several threads reach this — the keyboard hook, two timers, and every worker — so
        none of them may assert a state blindly. Recording outranks transcribing, because
        it is the one the user is actively doing.
        """
        self._set_state(_TRAY_STATE_FOR[self.ui_state()])

    def _notify(self, message: str) -> None:
        if self._tray is not None:
            self._tray.notify(message)

    def _report_error(self, detail: str, user_message: str) -> None:
        """Detail goes to the log; the user gets a sentence they can act on."""
        logger.error("%s: %s", user_message, detail)
        self._showing_error = True
        self._set_state(TrayState.ERROR)
        self._notify(user_message)
        self._clear_error_after(ERROR_DISPLAY_SECONDS)

    def _clear_error_after(self, seconds: float) -> None:
        self._cancel_error_reset()
        timer = threading.Timer(seconds, self._reset_after_error)
        timer.daemon = True
        timer.start()
        self._error_reset = timer

    def _cancel_error_reset(self) -> None:
        if self._error_reset is not None:
            self._error_reset.cancel()
            self._error_reset = None

    def _reset_after_error(self) -> None:
        self._error_reset = None
        self._showing_error = False
        self._settle_state()


def _describe_input_device(device: int | str | None) -> str:
    """A short name for the microphone in use, for the window's footer.

    Queried lazily and never allowed to fail: this is decoration, and PortAudio can throw
    for a dozen reasons that have nothing to do with whether dictation works.
    """
    try:
        import sounddevice as sd

        name = str(sd.query_devices(device, "input")["name"])
    except Exception:
        return "მიკროფონი"

    # MME truncates names at 31 characters, so they arrive already cut off mid-word. The
    # window has room for about fifteen more, and a name that runs long collides with the
    # shortcut hint on the other side of the footer.
    name = name.strip().split("(")[0].strip()
    return f"მიკროფონი: {name[:16].rstrip()}" if name else "მიკროფონი"


def _highest_take_number(directory: Path) -> int:
    """Continue numbering above whatever a previous session left behind."""
    try:
        names = [p.name for p in directory.glob(PENDING_PATTERN)]
    except OSError:
        return 0
    numbers = [int(m.group(1)) for name in names if (m := PENDING_NUMBER.search(name))]
    return max(numbers, default=0)
