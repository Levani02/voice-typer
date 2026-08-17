"""Entry point. Builds the app, shows the window, and shuts everything down cleanly.

Run it with pythonw.exe (or run.vbs) so no console window appears:

    .venv\\Scripts\\pythonw.exe main.py

Thread layout, which is not arbitrary:

* **Tk owns the main thread.** It has to — Tk refuses to run anywhere else.
* **pystray runs on a background thread.** It would also like the main thread, and only
  one of them can have it. The window is the status display the user actually sees, since
  Windows 11 hides new tray icons behind the "^" arrow by default, so the tray gives way.
* The keyboard hook, the auto-stop timers, and one worker per transcription bring the
  total to five or so. Everything they touch is either locked or read-only.
"""

from __future__ import annotations

import ctypes
import logging
import logging.handlers
import sys
import threading

from voice_typer.app import App
from voice_typer.config import LOGS_DIR, ConfigError, load_config
from voice_typer.overlay import OverlayWindow
from voice_typer.single_instance import SingleInstance
from voice_typer.tray import APP_NAME, TrayIcon

LOG_PATH = LOGS_DIR / "voice_typer.log"
WINDOW_POSITION_PATH = LOGS_DIR / "window.json"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
SW_HIDE = 0
DPI_PER_MONITOR_AWARE_V2 = -4

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


def make_dpi_aware() -> None:
    """Draw at the screen's real resolution instead of being stretched to it.

    Without this, Windows renders the window at 96 DPI and then scales the finished
    bitmap up — on a display at 125% that means every line and every letter is blown up
    by a quarter and resampled, which is exactly the soft, chunky look it produces.
    Declaring awareness hands the app the real pixels; `overlay.py` then multiplies its
    own measurements so the window stays the same physical size, only sharper.

    Must run before any window exists, which is why it is here and not in the overlay.
    """
    user32 = ctypes.windll.user32
    try:  # Windows 10 1703 and later — per-monitor, survives being dragged between screens
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_PER_MONITOR_AWARE_V2)):
            return
    except Exception:
        pass

    try:  # Windows 8.1
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass

    try:  # Windows Vista and later — system-wide scaling only
        user32.SetProcessDPIAware()
    except Exception as exc:
        logger.warning("could not become DPI aware, the window may look soft: %s", exc)


def hide_own_console() -> None:
    """Get rid of the black console window, if this process opened one.

    Double-clicking `main.py` runs it under `python.exe`, which comes with a console —
    and a dictation tool that leaves a terminal sitting on the desktop is not finished.
    `pythonw.exe` has no console to begin with, so this does nothing there.

    The check matters: when the app is started from an existing PowerShell, the console
    belongs to that shell, and hiding it would close the user's own terminal out from
    under them. Only a console this process owns is hidden.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        console = kernel32.GetConsoleWindow()
        if not console:
            return

        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(console, ctypes.byref(owner))
        if owner.value != kernel32.GetCurrentProcessId():
            return

        user32.ShowWindow(console, SW_HIDE)
        # Detach from the console as well as hiding it. A hidden console can still be
        # closed by Windows, and that sends a close event to every process attached to
        # it — which would take the app down with it, silently.
        kernel32.FreeConsole()
    except Exception as exc:
        logger.warning("could not hide the console window: %s", exc)


def show_dialog(message: str, icon: int = MB_ICONERROR) -> None:
    """The only way to reach the user before the window exists.

    Launched with pythonw there is no console, so a startup failure would otherwise be
    silent — the app would simply never appear.
    """
    ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, icon)


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
            MB_ICONWARNING,
        )
        return 1

    try:
        try:
            config = load_config()
        except ConfigError as exc:
            logger.error("startup failed: %s", exc)
            show_dialog(str(exc))
            return 1

        app = App(config)

        def quit_everything() -> None:
            app.shutdown()
            tray.stop()
            overlay.close()

        tray = build_tray(app, quit_everything)
        app.attach_tray(tray)
        app.set_quit_handler(quit_everything)

        app.start()
        overlay = OverlayWindow(
            app, WINDOW_POSITION_PATH, config.window_scale, config.content_scale
        )
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

    Under pythonw.exe there is no console, so an uncaught exception makes the app vanish
    with no window, no message, and nothing in the log — which is exactly what happened
    the first time the window was added.
    """
    try:
        return main()
    except Exception as exc:
        logging.getLogger(__name__).exception("the app could not start")
        show_dialog(
            f"voice-typer ვერ გაეშვა.\n\n{type(exc).__name__}: {exc}\n\n"
            f"დეტალები: logs\\voice_typer.log"
        )
        return 1


if __name__ == "__main__":
    sys.exit(_run_and_report_failures())
