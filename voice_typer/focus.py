"""Which application the user was typing in, and how to give it back the focus.

Pressing the hotkey never moves the focus, so none of this runs on that path. Clicking
the app's own record button does move it, and then the paste would land on a window with
nowhere to put it. That is what this module exists to undo.

The two systems answer "where was the user typing" with different things, so the token
passed around here is deliberately opaque:

* **Windows** — a window handle from `GetForegroundWindow`, restored with
  `SetForegroundWindow`.
* **macOS** — the process id of the frontmost application, restored by asking
  `NSRunningApplication` to activate it. There is no window handle to be had: a Mac hands
  out no reference to a window it does not own.

Everything here runs on a worker thread, which on macOS is the constraint that decides
the design. `NSRunningApplication` and `NSWorkspace.frontmostApplication` are documented
as safe to use from any thread; `NSApplication` is not, and is therefore never touched —
that is the same main-thread rule that killed this app once already, in `injector.py`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

from voice_typer.config import LOGS_DIR
from voice_typer.platform_support import IS_MACOS, IS_WINDOWS

if IS_MACOS:  # already resident: pynput pulls in Quartz at startup, which pulls in AppKit
    try:
        import AppKit
        import objc
    except Exception:  # pragma: no cover - macOS only
        AppKit = None
        objc = None
else:
    AppKit = None
    objc = None

logger = logging.getLogger(__name__)

# How long Windows is given to settle after the foreground change before the keystroke.
FOCUS_SETTLE_SECONDS = 0.06

# macOS activation is a request, granted asynchronously, and it returns before the target
# window has the caret. So the switch is waited for rather than assumed, and then given a
# moment more.
MACOS_ACTIVATION_TIMEOUT_SECONDS = 0.5
MACOS_ACTIVATION_POLL_SECONDS = 0.02
MACOS_FOCUS_SETTLE_SECONDS = 0.12

# A breadcrumb dropped just before each kind of macOS call that cannot be proven safe
# from here, and removed the moment that call returns. A main-thread assertion inside
# AppKit does not raise — it aborts the process — so a file left behind is the only
# evidence that would survive one, and finding it at startup switches that one call off.
# One crash, ever, instead of one per dictation.
#
# Each kind gets its own file. A single shared one would let the first frontmost read of
# the next run delete the record of an activation that crashed, and the app would walk
# into the same crash on every other launch.
#
# Nothing removes a file that was found at startup: a call that killed the process stays
# off until the user deletes it, and the log says which file that is. Only the first call
# of each kind is probed — a read that worked once on a worker thread is not going to
# start asserting on the hundredth.
_PROBE_KINDS = ("frontmost", "activate")
_PROVEN_CALLS: set[str] = set()


def _probe_path(kind: str) -> Path:
    return LOGS_DIR / f".focus_probe-{kind}"


_CRASHED_CALLS = {kind for kind in _PROBE_KINDS if _probe_path(kind).exists()}


def foreground_window() -> int:
    """Whatever is in front right now: a window handle, a process id, or 0 if unknown."""
    if IS_WINDOWS:
        return _windows_foreground_window()
    if IS_MACOS:
        return _macos_frontmost_pid()
    return 0


def is_our_window(token: int) -> bool:
    """Does that token belong to this application?"""
    if not token:
        return False
    if IS_WINDOWS:
        return _windows_window_is_ours(token)
    if IS_MACOS:
        return token == os.getpid()
    return False


def return_focus_to(target: int, *, started_from_our_window: bool = False) -> bool:
    """Hand the focus back if this app has it, and say whether pasting is now safe.

    False means the words must not be pasted: the focus is still here, and Cmd+V would go
    into a canvas that has nowhere to put it. The caller turns that into an error which
    leaves the transcript on the clipboard.

    `started_from_our_window` is True when the take was started by clicking this app's own
    record button, which is the only thing that moves the focus. Pressing the hotkey never
    does. It only matters on macOS, and only when macOS cannot be asked where the focus
    is — then this is the one fact left to reason from.
    """
    if IS_MACOS:
        return _macos_hand_back(target, started_from_our_window)

    if not target or not is_our_window(foreground_window()):
        return True  # the user is already where they want to be — the hotkey path

    if IS_WINDOWS:
        return _windows_return_focus(target)
    return True


# ------------------------------------------------------------------------------ Windows


def _windows_foreground_window() -> int:
    import ctypes

    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception as exc:
        logger.warning("could not read the foreground window: %s", exc)
        return 0


def _windows_window_is_ours(handle: int) -> bool:
    import ctypes

    try:
        user32 = ctypes.windll.user32
        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        return process_id.value == ctypes.windll.kernel32.GetCurrentProcessId()
    except Exception:
        return False


def _restore_foreground(handle: int) -> None:
    """Put focus back on the window the user was working in.

    Windows will not simply let one application steal the foreground, so if the direct
    request is refused we attach our input queue to the target window's thread, which
    makes the two count as one for the purposes of that rule, and try once more.
    """
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if user32.SetForegroundWindow(handle):
        return

    our_thread = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(handle, None)
    if not target_thread or target_thread == our_thread:
        return

    user32.AttachThreadInput(our_thread, target_thread, True)
    try:
        user32.SetForegroundWindow(handle)
        user32.SetFocus(handle)
    finally:
        user32.AttachThreadInput(our_thread, target_thread, False)


def _windows_return_focus(target: int) -> bool:
    """Always True: on Windows a paste that lands somewhere beats one that lands nowhere.

    This is the behaviour the app has shipped with, verified on a real desktop, so it is
    left exactly as it was. macOS is stricter because there the refusal is the norm
    rather than the exception — see `_macos_return_focus`.
    """
    try:
        _restore_foreground(target)
        time.sleep(FOCUS_SETTLE_SECONDS)
        logger.info("handed focus back to the window the recording started in")
    except Exception as exc:
        logger.warning("could not hand focus back: %s", exc)
    return True


# -------------------------------------------------------------------------------- macOS


@contextlib.contextmanager
def _autorelease():
    """PyObjC wants a pool around Objective-C work on a thread it did not create."""
    if objc is None:
        yield
        return
    with objc.autorelease_pool():
        yield


def _macos_focus_is_readable() -> bool:
    """Whether macOS can be asked which application is in front at all.

    It cannot when AppKit is missing, or when reading it is what killed the last run. The
    distinction between "the focus is elsewhere" and "the focus cannot be seen" is the
    whole point: both look like 0, and treating the second as the first means pasting
    blind into our own window and calling it a success.
    """
    return AppKit is not None and "frontmost" not in _CRASHED_CALLS


def _macos_hand_back(target: int, started_from_our_window: bool) -> bool:
    """Three questions in order: can the focus be read, has it moved, can it be returned."""
    if not _macos_focus_is_readable():
        if started_from_our_window:
            logger.warning(
                "macOS cannot be asked where the focus is and this take started in our own "
                "window — refusing to paste blind"
            )
            return False
        return True  # the hotkey never moved the focus, so pasting is still safe

    if not is_our_window(foreground_window()):
        return True  # the user is already where they want to be

    if not target:
        logger.warning("this app has the focus and no window is recorded to hand it back to")
        return False

    return _macos_return_focus(target)


def _macos_frontmost_pid() -> int:
    if AppKit is None or "frontmost" in _CRASHED_CALLS:
        return 0

    def read() -> int:
        with _autorelease():
            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            return int(app.processIdentifier()) if app is not None else 0

    try:
        return _run_probed("frontmost", read)
    except Exception as exc:
        logger.debug("could not read the frontmost application: %s", exc)
        return 0


def _macos_activate(app) -> None:
    """Ask the system to make `app` active, the way macOS 14 and later require it.

    `activateWithOptions_(NSApplicationActivateIgnoringOtherApps)` was the old spelling
    and is now ignored outright: since macOS 14 activation is a request, granted when it
    comes from the application that currently holds the focus. That is exactly the case
    here, and `activateFromApplication:options:` is how it is expressed — this app hands
    its turn to the one the user was typing in.
    """
    current = AppKit.NSRunningApplication.currentApplication()
    if hasattr(app, "activateFromApplication_options_"):
        app.activateFromApplication_options_(current, 0)
    else:  # macOS 13 and earlier, where cooperative activation does not exist yet
        app.activateWithOptions_(0)


def _run_probed(kind: str, action):
    """Run `action`, leaving a marker on disk for its duration. See `_probe_path`.

    Only the first call of each kind pays for the two file operations; after that the
    call has proven itself for the life of the process. Each kind touches only its own
    file, so no call can erase the record of another one that crashed.
    """
    if kind in _PROVEN_CALLS:
        return action()

    probe = _probe_path(kind)
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.touch()
    except OSError as exc:
        logger.debug("could not write the focus probe: %s", exc)

    try:
        result = action()
    finally:
        with contextlib.suppress(OSError):
            probe.unlink(missing_ok=True)

    _PROVEN_CALLS.add(kind)
    return result


def _wait_until_focus_has_left() -> bool:
    deadline = time.monotonic() + MACOS_ACTIVATION_TIMEOUT_SECONDS
    while is_our_window(foreground_window()):
        if time.monotonic() >= deadline:
            return False
        time.sleep(MACOS_ACTIVATION_POLL_SECONDS)

    time.sleep(MACOS_FOCUS_SETTLE_SECONDS)
    return True


def _macos_return_focus(target: int) -> bool:
    if AppKit is None:
        logger.warning("AppKit is unavailable, so the focus cannot be handed back")
        return False

    if "activate" in _CRASHED_CALLS:
        logger.warning(
            "a previous run died handing the focus back, so that is switched off — delete "
            "%s to try it again",
            _probe_path("activate"),
        )
        return False

    try:
        with _autorelease():
            app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(target)
            if app is None:
                logger.info("the application this take started in is gone (pid %d)", target)
                return False
            _run_probed("activate", lambda: _macos_activate(app))
    except Exception as exc:
        logger.warning("could not hand focus back: %s", exc)
        return False

    if not _wait_until_focus_has_left():
        logger.warning("the application the recording started in did not come back to the front")
        return False

    logger.info("handed focus back to the application the recording started in")
    return True
