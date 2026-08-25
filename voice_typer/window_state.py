"""Where the card was left, and whether that place still exists.

Split out of `overlay.py` because none of it needs a window. Reading a file that may be
corrupt, deciding whether a remembered position is still reachable, and writing the
result back are three plain decisions that were previously only reachable through a live
Tk session — which meant they were only ever tested on a machine with a desktop, and not
at all in CI.

The window keeps what genuinely needs Tk: measuring its own card, asking the screen how
big it is, and reporting where it currently sits.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SavedWindow:
    """What the last session left behind. `position` is absent when the file is new,
    unreadable, or was written by a version that did not record one."""

    position: tuple[int, int] | None = None
    collapsed: bool = False
    rewrite_mode: bool = False


def read(path: Path) -> SavedWindow:
    """Whatever was written last time, or the defaults.

    Never raises. A file that cannot be read means the window opens in its default
    corner, unfolded, in words mode — which is a fresh start, not a failure.
    """
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SavedWindow()
    if not isinstance(saved, dict):
        return SavedWindow()
    return SavedWindow(
        position=_position_in(saved),
        collapsed=bool(saved.get("collapsed", False)),
        rewrite_mode=bool(saved.get("rewrite_mode", False)),
    )


def _position_in(saved: dict) -> tuple[int, int] | None:
    """Both coordinates or neither. A file carrying only an `x` is not half a position."""
    try:
        return int(saved["x"]), int(saved["y"])
    except (KeyError, ValueError, TypeError):
        return None


def write(path: Path, state: SavedWindow) -> bool:
    """Remember the state for next time. Returns whether it was actually written, so the
    caller only treats a value as saved once it really is on disk — otherwise a full
    disk would be silently forgotten and every later comparison would think it agreed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_as_json(state)), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not remember the window position: %s", exc)
        return False
    return True


def _as_json(state: SavedWindow) -> dict:
    body: dict[str, object] = {
        "collapsed": state.collapsed,
        "rewrite_mode": state.rewrite_mode,
    }
    if state.position is not None:
        body["x"], body["y"] = state.position
    return body


def choose_position(
    position: tuple[int, int] | None,
    *,
    default: tuple[int, int],
    width: int,
    margin: int,
    bounds: tuple[int, int, int, int],
) -> tuple[int, int]:
    """The remembered position if the card would still be reachable there, else the
    default corner.

    `bounds` is the whole desktop, so a card parked on a second monitor stays there.
    Enough of the card must remain on screen to be grabbed and dragged back; a position
    saved before a monitor was unplugged would otherwise leave the window somewhere the
    mouse cannot go.
    """
    if position is None:
        return default
    x, y = position
    left, top, right, bottom = bounds
    on_screen_x = left - width + margin < x < right - margin
    on_screen_y = top - margin < y < bottom - margin
    return (x, y) if on_screen_x and on_screen_y else default


def clamp_to_desktop(
    position: tuple[int, int],
    *,
    width: int,
    height: int,
    bounds: tuple[int, int, int, int],
) -> tuple[int, int]:
    """Pull a card that is about to grow back inside the desktop.

    Unfolding happens in place, so a strip parked in the bottom-right corner would
    otherwise become a card whose lower half is off the screen — and the default corner
    is exactly where this window starts. Unlike `choose_position` this never falls back
    to somewhere else: the user put the card here, so it stays as close to here as fits.
    """
    left, top, right, bottom = bounds
    x, y = position
    return max(left, min(x, right - width)), max(top, min(y, bottom - height))
