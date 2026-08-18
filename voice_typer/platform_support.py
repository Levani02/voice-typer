"""The handful of things that are simply different on Windows and on macOS.

Everything here has the same shape on both systems and a different implementation
underneath: opening a folder, showing a message before any window exists, and the two
Windows-only startup chores that have no counterpart on a Mac.

Window and focus behaviour is *not* here — those live beside the code that owns them, in
`window_platform.py` and `injector.py`, because the reasoning only makes sense next to it.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

PLATFORM_NAME = "Windows" if IS_WINDOWS else "macOS" if IS_MACOS else sys.platform

APP_TITLE = "voice-typer"

# MessageBoxW icons. Named here so main.py does not need ctypes just to pick one.
DIALOG_ERROR = 0x10
DIALOG_WARNING = 0x30

# A dialog nobody is looking at must not hold the process open forever.
OSASCRIPT_TIMEOUT_SECONDS = 120


def tray_is_supported() -> bool:
    """Whether a tray icon can run on a background thread.

    On Windows it can. On macOS the status-bar backend drives an AppKit run loop, and
    AppKit refuses to run anywhere but the main thread — which Tk already owns. Rather
    than fight over it, the Mac build goes without the tray: the recorder window is the
    status display either way, and on macOS it is never hidden behind an arrow.
    """
    return IS_WINDOWS


def open_path(path: Path) -> None:
    """Open a file or folder in whatever the system uses for it."""
    try:
        if IS_WINDOWS:
            import os

            os.startfile(path)  # a fixed path, never taken from config
        elif IS_MACOS:
            subprocess.run(["/usr/bin/open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as exc:
        logger.warning("could not open %s: %s", path, exc)


def _escape_for_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _show_dialog_macos(message: str, icon: int) -> None:
    """AppleScript is the only way to put a dialog on screen before Tk exists."""
    kind = "caution" if icon == DIALOG_WARNING else "stop"
    script = (
        f'display dialog "{_escape_for_applescript(message)}" '
        f'with title "{APP_TITLE}" buttons {{"OK"}} default button "OK" with icon {kind}'
    )
    subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        check=False,
        timeout=OSASCRIPT_TIMEOUT_SECONDS,
    )


def show_dialog(message: str, icon: int = DIALOG_ERROR) -> None:
    """The only way to reach the user before the window exists.

    Started without a console — `pythonw.exe` on Windows, a double-clicked `.command` on
    macOS — a startup failure would otherwise be completely silent: the app would simply
    never appear.
    """
    try:
        if IS_WINDOWS:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, icon)
            return
        if IS_MACOS:
            _show_dialog_macos(message, icon)
            return
    except Exception as exc:
        logger.warning("could not show the startup dialog: %s", exc)

    # Better on the console than nowhere at all.
    print(f"{APP_TITLE}: {message}", file=sys.stderr)


def make_dpi_aware() -> None:
    """Draw at the screen's real resolution instead of being stretched to it.

    Without this, Windows renders the window at 96 DPI and then scales the finished
    bitmap up — on a display at 125% that means every line and every letter is blown up
    by a quarter and resampled, which is exactly the soft, chunky look it produces.
    Declaring awareness hands the app the real pixels; `overlay.py` then multiplies its
    own measurements so the window stays the same physical size, only sharper.

    Must run before any window exists, which is why it is called from `main` and not from
    the overlay. macOS hands every application a Retina-correct drawing context already,
    so there is nothing to declare there.
    """
    if not IS_WINDOWS:
        return

    import ctypes

    dpi_per_monitor_aware_v2 = -4
    user32 = ctypes.windll.user32
    try:  # Windows 10 1703 and later — per-monitor, survives being dragged between screens
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(dpi_per_monitor_aware_v2)):
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

    macOS has no equivalent — a `.command` file always opens Terminal, and the launcher
    closes that window itself.
    """
    if not IS_WINDOWS:
        return

    import ctypes

    sw_hide = 0
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

        user32.ShowWindow(console, sw_hide)
        # Detach from the console as well as hiding it. A hidden console can still be
        # closed by Windows, and that sends a close event to every process attached to
        # it — which would take the app down with it, silently.
        kernel32.FreeConsole()
    except Exception as exc:
        logger.warning("could not hide the console window: %s", exc)
