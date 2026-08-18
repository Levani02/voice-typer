"""The window behaviour that Tk cannot express by itself, per operating system.

Three things the recorder window needs are outside Tk's vocabulary, and each is spelled
differently on the two systems:

* **never taking focus**, so clicking a button here does not pull the caret out of
  whatever the user was typing into
* **real transparency**, which is what gives the card rounded corners instead of a black
  box behind them
* **the true size of the desktop**, so a window parked on a second monitor is not judged
  off-screen and dragged back

Every function degrades to something harmless when the platform will not co-operate: the
window is worth having slightly wrong, and not worth crashing over.
"""

from __future__ import annotations

import logging
import tkinter as tk

from voice_typer.platform_support import IS_MACOS, IS_WINDOWS

logger = logging.getLogger(__name__)

STANDARD_DPI = 96.0

# Windows window styles.
_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080
_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_SWP_SHOWWINDOW = 0x0040

# GetSystemMetrics indices for the bounding box around every monitor.
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79

# The Tk colour name that means "let the desktop show through" on Aqua.
_MACOS_TRANSPARENT = "systemTransparent"


def display_scale() -> float:
    """How much larger than 100% the display is set to run.

    On Windows this is read from the desktop rather than from Tk: with the process
    DPI-aware, it is the number Windows is actually using, and it is what keeps the window
    the same physical size while drawing it at full resolution.

    macOS never exposes a scaling factor to an application — a Retina screen is handled
    below the drawing API, so Tk's coordinates are already the right physical size and
    multiplying them again would double the window.
    """
    if not IS_WINDOWS:
        return 1.0

    import ctypes

    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
    except Exception:
        try:
            device = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(device, 88)  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, device)
        except Exception:
            return 1.0
    return max(1.0, dpi / STANDARD_DPI) if dpi else 1.0


def desktop_bounds() -> tuple[int, int, int, int] | None:
    """The rectangle around every monitor, or None if the system will not say.

    `winfo_screenwidth` reports only the primary display, so a window the user had parked
    on a second monitor was always judged off-screen and dragged back. Windows can be
    asked directly; on macOS the caller falls back to the primary screen.
    """
    if not IS_WINDOWS:
        return None

    import ctypes

    try:
        user32 = ctypes.windll.user32
        left = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        top = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        width = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        height = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
        if width and height:
            return left, top, left + width, top + height
    except Exception as exc:
        logger.warning("could not measure the desktop, using the primary screen: %s", exc)
    return None


def _toplevel_handle(window: tk.Misc) -> int:
    """The window Windows actually manages, not Tk's inner child.

    `winfo_id()` returns a WS_CHILD window. WS_EX_NOACTIVATE and WS_EX_TOOLWINDOW are
    top-level styles and do nothing on a child, so setting them there looked correct and
    achieved nothing: every click on the card still took the foreground, which is why the
    focus had to be handed back afterwards. `wm_frame()` is the real one.
    """
    try:
        return int(window.wm_frame(), 16)
    except Exception:
        return window.winfo_id()


def _make_non_activating_windows(window: tk.Misc) -> None:
    """Windows caches a window's frame, so the style change only takes effect once
    SetWindowPos is told the frame changed — and the same call puts the window on top."""
    import ctypes

    handle = _toplevel_handle(window)
    user32 = ctypes.windll.user32
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)

    style = get_long(handle, _GWL_EXSTYLE)
    set_long(handle, _GWL_EXSTYLE, style | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW)
    user32.SetWindowPos(
        handle,
        _HWND_TOPMOST,
        0,
        0,
        0,
        0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED | _SWP_SHOWWINDOW,
    )


def _make_non_activating_macos(window: tk.Misc) -> None:
    """A "help" window with `noActivates` floats above everything and never takes focus.

    This is Tk's own escape hatch to the Carbon window classes; the name has been
    `::tk::unsupported::MacWindowStyle` for twenty years and it is how every Tk utility
    palette on a Mac is built.
    """
    window.tk.call("::tk::unsupported::MacWindowStyle", "style", window._w, "help", "noActivates")


def make_non_activating(window: tk.Misc) -> None:
    """Stop the window from becoming the foreground window when it is clicked."""
    try:
        if IS_WINDOWS:
            _make_non_activating_windows(window)
        elif IS_MACOS:
            _make_non_activating_macos(window)
    except Exception as exc:
        logger.warning("could not make the window non-activating: %s", exc)


def apply_transparency(root: tk.Tk, key_colour: str, fallback_colour: str) -> str:
    """Make the area outside the card see-through, and say what to paint it with.

    Windows does this by nominating one colour as invisible wherever it appears. macOS
    makes the whole window transparent instead and asks for a named system colour. If
    neither works the card is painted onto an opaque backdrop in its own top colour —
    the corners stop being round, which is a blemish, not a failure.
    """
    if IS_WINDOWS:
        try:
            root.attributes("-transparentcolor", key_colour)
            return key_colour
        except tk.TclError as exc:
            logger.warning("transparency is unavailable, the card will have corners: %s", exc)
            return fallback_colour

    if IS_MACOS:
        try:
            root.attributes("-transparent", True)
            return _MACOS_TRANSPARENT
        except tk.TclError as exc:
            logger.warning("transparency is unavailable, the card will have corners: %s", exc)

    return fallback_colour
