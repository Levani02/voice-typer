"""Put something on the desktop that starts the app.

A downloaded executable lands in whatever folder the browser chose. Without an icon on
the desktop, starting the app a second time means remembering where that was — which is
the point at which a tool stops being used.

The two systems want different objects, and neither is a file this code can simply write:

* **Windows** wants a `.lnk`, which is a structured binary only the shell knows how to
  build. PowerShell is asked to make it, because it is present on every Windows and can
  also resolve the real Desktop folder — which is not always `%USERPROFILE%\\Desktop`,
  since OneDrive moves it.
* **macOS** wants an alias, and a symlink is close enough: Finder shows it with the
  application's own icon and opens the bundle when it is double-clicked.

Nothing here is allowed to fail loudly. An app that refuses to start because it could not
decorate the desktop would be worse than one with no icon.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from voice_typer.config import PROJECT_ROOT
from voice_typer.platform_support import IS_WINDOWS

logger = logging.getLogger(__name__)

SHORTCUT_NAME = "voice-typer"
DESCRIPTION = "voice-typer — ილაპარაკე, ტექსტი გამოჩნდება"

# PowerShell is asked to do one thing and print one line. Long enough for a cold start of
# the shell on a slow machine, short enough that nobody watches a frozen window.
POWERSHELL_TIMEOUT_SECONDS = 30


def _macos_app_bundle() -> Path | None:
    """The .app this code is running inside, if it is running inside one.

    `sys.executable` points at `voice-typer.app/Contents/MacOS/voice-typer`, so the bundle
    is three levels up. Anything else means the app was started from source.
    """
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    bundle = executable.parent.parent.parent
    return bundle if bundle.suffix == ".app" else None


def _what_to_start() -> Path | None:
    """The thing a double-click should open.

    Packaged, that is the executable itself — the whole app is one object. From source it
    is the launcher script, which starts the app without leaving a terminal behind.
    """
    if getattr(sys, "frozen", False):
        return _macos_app_bundle() or Path(sys.executable).resolve()

    launcher = PROJECT_ROOT / ("run.vbs" if IS_WINDOWS else "run.command")
    return launcher if launcher.is_file() else None


def _quote_for_powershell(text: str) -> str:
    """Single quotes, with any single quote inside doubled — PowerShell's own escaping."""
    return "'" + str(text).replace("'", "''") + "'"


def _create_windows_shortcut(target: Path) -> Path | None:
    """Build a .lnk through the shell, and let the shell say where the desktop is."""
    icon = PROJECT_ROOT / "assets" / "voice-typer.ico"
    # A packaged .exe carries its icon inside it, so pointing at a file that will not be
    # there beside the executable would leave the shortcut blank.
    icon_line = (
        f"$link.IconLocation = {_quote_for_powershell(icon)};"
        if icon.is_file() and not getattr(sys, "frozen", False)
        else ""
    )

    script = (
        "$desktop = [Environment]::GetFolderPath('Desktop');"
        f"$path = Join-Path $desktop '{SHORTCUT_NAME}.lnk';"
        "$link = (New-Object -ComObject WScript.Shell).CreateShortcut($path);"
        f"$link.TargetPath = {_quote_for_powershell(target)};"
        f"$link.WorkingDirectory = {_quote_for_powershell(target.parent)};"
        f"$link.Description = {_quote_for_powershell(DESCRIPTION)};"
        f"{icon_line}"
        "$link.Save();"
        "Write-Output $path"
    )

    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=POWERSHELL_TIMEOUT_SECONDS,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no black flash
    )
    if result.returncode != 0:
        logger.warning("could not create the desktop shortcut: %s", result.stderr.strip())
        return None

    printed = result.stdout.strip()
    return Path(printed) if printed else None


def _create_macos_alias(target: Path, desktop: Path) -> Path | None:
    """A symlink named after the app. Finder treats it as the app and shows its icon."""
    link = desktop / (target.name if target.suffix == ".app" else f"{SHORTCUT_NAME}.command")
    if link.is_symlink() or link.exists():
        link.unlink()  # replacing our own link, never a file the user put there
    link.symlink_to(target)
    return link


def create_desktop_shortcut(desktop: Path | None = None) -> Path | None:
    """Put an icon on the desktop. Returns where it went, or None if it could not.

    `desktop` overrides where the icon goes and exists for the tests; left out, each
    system is asked for its own desktop folder.
    """
    target = _what_to_start()
    if target is None:
        logger.info("nothing to point a desktop shortcut at")
        return None

    try:
        if IS_WINDOWS:
            return _create_windows_shortcut(target)

        folder = desktop or (Path.home() / "Desktop")
        if not folder.is_dir():
            logger.info("no desktop folder at %s", folder)
            return None
        return _create_macos_alias(target, folder)
    except Exception as exc:
        # An icon is a convenience. Never let it stop the app from starting.
        logger.warning("could not create the desktop shortcut: %s", exc)
        return None
