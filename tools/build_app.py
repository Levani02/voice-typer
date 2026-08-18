"""Build the standalone application — one file the user can run without installing Python.

    Windows:  .venv\\Scripts\\python.exe tools\\build_app.py   ->  dist/voice-typer.exe
    macOS:    .venv/bin/python tools/build_app.py             ->  dist/voice-typer.app

The result carries its own Python and every library with it. Nothing is installed on the
machine that runs it, and nothing is left behind if it is deleted.

Two deliberate differences between the platforms:

* Windows gets a single .exe. Settings, the key and the logs live beside it, so the whole
  thing can be copied to another machine on a memory stick and still work.
* macOS gets a .app bundle. A bundle is meant to be read-only, so the same three things go
  to `~/Library/Application Support/voice-typer` instead — see `config._project_root`.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINT = PROJECT_ROOT / "main.py"
APP_NAME = "voice-typer"

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# PyInstaller separates a bundled file from its destination with ';' on Windows and ':'
# everywhere else. Getting this wrong silently bundles nothing.
DATA_SEPARATOR = ";" if IS_WINDOWS else ":"

# pystray and sounddevice both load their real implementation by name at run time, which
# is invisible to the dependency scanner. Named here or the built app starts and then
# fails the moment it tries to draw an icon or open the microphone.
HIDDEN_IMPORTS = ["pystray._win32"] if IS_WINDOWS else ["pystray._darwin"]

# macOS hands the microphone to nobody who has not said why they want it. Without
# NSMicrophoneUsageDescription in the bundle there is no permission prompt and no entry to
# switch on in System Settings: the stream opens, every sample arrives as silence, and the
# menu-bar recording indicator never lights. The upload then succeeds and comes back with
# no text, so the user is told "the recording may be silent" — a long way from the cause.
# That is exactly how the v0.1.0 build failed, and PyInstaller's default Info.plist carries
# none of these keys.
MACOS_USAGE_DESCRIPTIONS = {
    "NSMicrophoneUsageDescription": (
        "voice-typer records what you say so it can be typed out as text."
    ),
    # `platform_support.show_dialog` reaches the user through osascript before any window
    # exists, and driving another application that way is a permission of its own.
    "NSAppleEventsUsageDescription": ("voice-typer shows a system dialog when it cannot start."),
}


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401 — imported for the side effect of existing
    except ImportError:
        print("PyInstaller is not installed here. Installing it now.")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=True,
        )


def clean_previous_build() -> None:
    """A stale build is worse than none — it looks finished and runs old code."""
    for folder in ("build", "dist"):
        target = PROJECT_ROOT / folder
        if target.exists():
            shutil.rmtree(target)
    spec = PROJECT_ROOT / f"{APP_NAME}.spec"
    spec.unlink(missing_ok=True)


def build_command() -> list[str]:
    icon = PROJECT_ROOT / "assets" / ("voice-typer.ico" if IS_WINDOWS else "voice-typer.png")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--windowed",  # no console window on either system
        "--add-data",
        f"{PROJECT_ROOT / 'config.json'}{DATA_SEPARATOR}.",
        "--collect-all",
        "sounddevice",
    ]

    if IS_WINDOWS:
        command.append("--onefile")  # a single portable file
    if icon.is_file():
        command += ["--icon", str(icon)]
    for name in HIDDEN_IMPORTS:
        command += ["--hidden-import", name]

    command.append(str(ENTRY_POINT))
    return command


def declared_usage_descriptions(bundle: Path) -> set[str]:
    """Which of the usage strings the finished bundle actually carries.

    Read back from the bundle rather than trusted, because getting this wrong is invisible
    at build time and only shows up as a silent recording on someone else's Mac.
    """
    plist_path = bundle / "Contents" / "Info.plist"
    try:
        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        print(f"Could not read {plist_path}: {exc}")
        return set()

    return {name for name in MACOS_USAGE_DESCRIPTIONS if plist.get(name)}


def declare_macos_permissions(bundle: Path) -> None:
    """Write the usage strings into the bundle, then sign it again.

    The order is forced. PyInstaller signs the bundle ad-hoc as its last act, and changing
    any file inside afterwards breaks that signature — an Apple Silicon Mac then refuses to
    launch the app at all, which trades a silent microphone for an app that does not open.
    So the edit has to be followed by a fresh signature.
    """
    plist_path = bundle / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)

    plist.update(MACOS_USAGE_DESCRIPTIONS)
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle)

    subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", "-", str(bundle)],
        check=True,
    )
    print("Declared the microphone in Info.plist and signed the bundle again.")


def finish_macos_bundle(bundle: Path) -> int:
    """Add what PyInstaller leaves out, and refuse to ship a bundle still missing it."""
    try:
        declare_macos_permissions(bundle)
    except (OSError, plistlib.InvalidFileException, subprocess.CalledProcessError) as exc:
        print(f"\nCould not declare the microphone permission: {exc}")
        return 1

    missing = set(MACOS_USAGE_DESCRIPTIONS) - declared_usage_descriptions(bundle)
    if missing:
        print(
            f"\nThe bundle still does not declare: {', '.join(sorted(missing))}."
            "\nShipping it would record silence, so the build stops here."
        )
        return 1
    return 0


def report_result() -> int:
    built = PROJECT_ROOT / "dist" / (f"{APP_NAME}.exe" if IS_WINDOWS else f"{APP_NAME}.app")
    if not built.exists():
        print(f"\nThe build finished but {built.name} is not there. Nothing to ship.")
        return 1

    if built.is_file():
        size = built.stat().st_size / 1_000_000
        print(f"\nBuilt {built} ({size:.0f} MB)")
    else:
        print(f"\nBuilt {built}")

    if IS_MACOS:
        print(
            "\nmacOS will refuse to open it until it is either signed or allowed by hand:"
            "\n  right-click the app, choose Open, then Open again in the warning."
            "\nIt also needs Accessibility and Microphone permission — System Settings >"
            "\nPrivacy & Security. Without Accessibility the hotkey does nothing at all."
        )
    return 0


def main() -> int:
    if not ENTRY_POINT.is_file():
        print(f"Cannot find {ENTRY_POINT}. Run this from the project it belongs to.")
        return 1

    ensure_pyinstaller()
    clean_previous_build()

    result = subprocess.run(build_command(), cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print("\nThe build failed. The reason is in the output above.")
        return result.returncode

    if IS_MACOS:
        bundle = PROJECT_ROOT / "dist" / f"{APP_NAME}.app"
        if not bundle.is_dir():
            print(f"\nThe build finished but {bundle.name} is not there. Nothing to ship.")
            return 1
        failed = finish_macos_bundle(bundle)
        if failed:
            return failed

    return report_result()


if __name__ == "__main__":
    sys.exit(main())
