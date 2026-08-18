"""Entry point. Builds the app, shows the window, and shuts everything down cleanly.

Start it without a console, so no terminal is left sitting on the desktop:

    Windows:  .venv\\Scripts\\pythonw.exe main.py   (or run.vbs)
    macOS:    .venv/bin/python main.py              (or run.command)

Thread layout, which is not arbitrary:

* **Tk owns the main thread.** It has to — Tk refuses to run anywhere else.
* **pystray runs on a background thread, on Windows only.** It would also like the main
  thread, and only one of them can have it. The window is the status display the user
  actually sees, since Windows 11 hides new tray icons behind the "^" arrow by default,
  so the tray gives way. On macOS the status-bar backend insists on the main thread and
  cannot give way, so that build goes without a tray altogether.
* The keyboard hook, the auto-stop timers, and one worker per transcription bring the
  total to five or so. Everything they touch is either locked or read-only.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading

from voice_typer.app import App
from voice_typer.config import (
    LOGS_DIR,
    Config,
    ConfigError,
    MissingApiKeyError,
    ensure_settings_file,
    load_config,
)
from voice_typer.first_run import ask_for_api_key
from voice_typer.overlay import OverlayWindow
from voice_typer.platform_support import (
    DIALOG_ERROR,
    DIALOG_WARNING,
    hide_own_console,
    make_dpi_aware,
    show_dialog,
    tray_is_supported,
)
from voice_typer.single_instance import SingleInstance
from voice_typer.tray import TrayIcon

LOG_PATH = LOGS_DIR / "voice_typer.log"
WINDOW_POSITION_PATH = LOGS_DIR / "window.json"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Rotating file log. Never contains the API key; transcripts only at DEBUG."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("PIL").setLevel(logging.WARNING)  # it logs every image plugin it finds


def _settings_or_none() -> Config | None:
    """The settings, asking for the API key first if this is a first run.

    A missing key is the one startup failure the app can repair by itself, so it is the
    only one that opens a window instead of a message. Every other bad setting is a
    mistake the user has to correct in the file.
    """
    ensure_settings_file()
    try:
        return load_config()
    except MissingApiKeyError:
        logger.info("no API key yet — asking for one")
    except ConfigError as exc:
        logger.error("startup failed: %s", exc)
        show_dialog(str(exc))
        return None

    if not ask_for_api_key():
        logger.info("the first-run window was closed without a key")
        return None

    try:
        return load_config()
    except ConfigError as exc:
        logger.error("startup failed after the key was entered: %s", exc)
        show_dialog(str(exc))
        return None


def build_tray(app: App, quit_everything) -> TrayIcon:
    return TrayIcon(
        on_retry=app.retry_last,
        on_open_logs=app.open_logs,
        on_open_settings=app.open_settings,
        on_quit=quit_everything,
        usage_text=app.usage_text,
    )


def main() -> int:
    setup_logging()
    hide_own_console()
    make_dpi_aware()

    lock = SingleInstance()
    if not lock.acquire():
        show_dialog(
            "voice-typer უკვე გაშვებულია.\n\n"
            "მისი ფანჯარა ეკრანზე უნდა იყოს — თუ ვერ ხედავ, ეკრანის კიდეს მიღმა "
            "გადაათრიე. ორი ასლი ერთდროულად ვერ იმუშავებს: ორივე ჩაწერდა და "
            "ორჯერ გადაიხდიდი.",
            DIALOG_WARNING,
        )
        return 1

    try:
        config = _settings_or_none()
        if config is None:
            return 1

        app = App(config)
        tray: TrayIcon | None = None

        def quit_everything() -> None:
            app.shutdown()
            if tray is not None:
                tray.stop()
            overlay.close()

        if tray_is_supported():
            tray = build_tray(app, quit_everything)
            app.attach_tray(tray)
        app.set_quit_handler(quit_everything)

        app.start()
        overlay = OverlayWindow(
            app, WINDOW_POSITION_PATH, config.window_scale, config.content_scale
        )
        if tray is not None:
            threading.Thread(target=tray.run, name="tray", daemon=True).start()

        try:
            overlay.run()  # blocks until the window is closed
        except KeyboardInterrupt:
            logger.info("interrupted from the console")
        finally:
            quit_everything()

        return 0
    finally:
        lock.release()


def _run_and_report_failures() -> int:
    """Nothing may fail silently.

    Started without a console there is nowhere for a traceback to go, so an uncaught
    exception would make the app vanish with no window, no message, and nothing in the
    log — which is exactly what happened the first time the window was added.
    """
    try:
        return main()
    except Exception as exc:
        logging.getLogger(__name__).exception("the app could not start")
        show_dialog(
            f"voice-typer ვერ გაეშვა.\n\n{type(exc).__name__}: {exc}\n\nდეტალები: {LOG_PATH}",
            DIALOG_ERROR,
        )
        return 1


if __name__ == "__main__":
    sys.exit(_run_and_report_failures())
