"""Entry point. Builds the app, hands the main thread to the tray icon, and shuts down cleanly.

Run it with pythonw.exe (or run.vbs) so no console window appears:

    .venv\\Scripts\\pythonw.exe main.py
"""

from __future__ import annotations

import ctypes
import logging
import logging.handlers
import sys

from voice_typer.app import App
from voice_typer.config import LOGS_DIR, ConfigError, load_config
from voice_typer.tray import APP_NAME, TrayIcon

LOG_PATH = LOGS_DIR / "voice_typer.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
MB_ICONERROR = 0x10


def setup_logging() -> None:
    """Rotating file log. Never contains the API key; transcripts only at DEBUG."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.DEBUG, handlers=[handler])


def show_error_dialog(message: str) -> None:
    """The only way to reach the user before the tray icon exists.

    Launched with pythonw there is no console, so a startup failure would otherwise be
    silent — the app would simply never appear.
    """
    ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, MB_ICONERROR)


def main() -> int:
    setup_logging()
    logger = logging.getLogger(__name__)

    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("startup failed: %s", exc)
        show_error_dialog(str(exc))
        return 1

    app = App(config)
    tray = TrayIcon(
        on_retry=app.retry_last,
        on_open_logs=app.open_logs,
        on_open_settings=app.open_settings,
        on_quit=lambda: (app.shutdown(), tray.stop()),
        usage_text=app.usage_text,
    )
    app.attach_tray(tray)

    try:
        app.start()
    except ValueError as exc:  # an unrecognised hotkey name in config.json
        logger.error("startup failed: %s", exc)
        show_error_dialog(str(exc))
        return 1

    try:
        tray.run()  # blocks until the tray menu's Quit is chosen
    except KeyboardInterrupt:
        logger.info("interrupted from the console")
    finally:
        app.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
