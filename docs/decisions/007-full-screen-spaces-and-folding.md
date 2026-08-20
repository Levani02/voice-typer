# 007 — Staying visible over a full-screen application, and folding out of the way

**Date:** 2026-08-20
**Status:** Accepted; the folding is verified on Windows, the Spaces behaviour is unverified

## Context

Two complaints from the same macOS session, pulling in opposite directions.

**The window disappeared.** Putting Chrome into full screen hid the recorder, and it only
came back when the window was forced to lay itself out again. macOS full screen is not a
larger window — it is a Space of its own. A window belongs to the Space it was created in
and stays there. `-topmost` does not help: Tk's "always on top" is `NSFloatingWindowLevel`,
which is the top of *this* Space, and a full-screen window is above it in another.

**The window was in the way.** A 520-pixel card parked over whatever the user is reading
is a lot of screen for something whose whole job is to say "I am listening". The user's own
proposal was a fold button, with one condition attached: *F9 has to keep working at every
level.*

## Decision

**1. The window declares that it belongs in every Space.**
`window_platform.float_over_full_screen` sets the NSWindow's collection behaviour to
`canJoinAllSpaces | stationary | fullScreenAuxiliary` and raises its level to
`NSStatusWindowLevel` (25) — the level the menu bar uses, and above a full-screen window.
It runs once, after `root.update()`: there is no NSWindow to configure until Tk has made one.

`NSApplication` is only ever touched from the main thread, and the function refuses to run
anywhere else. An AppKit main-thread assertion does not raise — it kills the process. This
app has been killed that way once already (ADR 004), and a future caller reaching for this
from a worker thread is exactly how it would happen again.

Windows needs none of this: a `WS_EX_TOOLWINDOW` topmost window already draws over a
maximised or full-screen window.

**2. The card folds to a strip, and folding touches nothing else.**
A chevron beside the F9 badge collapses the card to 196×56 design pixels carrying the state
light, the timer, and the way back. The same item is in the right-click menu. The choice is
written next to the window position and restored at the next launch.

The hotkey listener has never known this window exists, so F9 works folded, unfolded, and
behind a full-screen application alike — the condition the user attached is satisfied by
the architecture rather than by anything added here.

Folding repaints rather than hides: the canvas is cleared, every item id, button and meter
bar is reset, and the other shape is drawn at the same corner. A stale canvas id in Tk is
not an error — it is a silent no-op, which is how a window ends up looking frozen. The
corner is clamped to the desktop on the way back out, because the default position is the
bottom-right and unfolding there would push most of the card off the screen.

## Alternatives rejected

**A second, smaller window for the folded state.** Tk allows one root per process, and a
`Toplevel` would need every platform attribute applied to it again — non-activating,
transparency, Spaces, level. Four things to get right twice, for one card that has two sizes.

**`withdraw()` and `deiconify()` to swap shapes.** On Windows a borderless window that is
hidden and shown again can come back unmapped, which is as useful as no window at all. The
comment in `overlay.py.__init__` that says so is older than this change.

**Hiding the window entirely when folded, leaving only the tray icon.** Windows 11 hides
new tray icons behind the "^" arrow, which is the reason this window exists at all. It
would also drop the state light, and "is it listening" is the one thing worth a glance.

**Raising the level without the collection behaviour.** A higher level moves a window up
within a Space; it does not put it in another one. Both are needed, and each alone reads
like a fix that did nothing.

**`NSMainMenuWindowLevel` or higher.** Above the menu bar is further than this needs to go.
`NSStatusWindowLevel` clears a full-screen window and leaves the system's own furniture on
top of us, which is the right relationship.

## What is not known

No Mac is available on this machine. The Spaces behaviour is reasoned from documented
NSWindow semantics and verified only in the sense that it degrades safely: if AppKit
refuses, `float_over_full_screen` logs a warning and the window is exactly as visible as it
was before. The folding is verified on Windows with screenshots, in both shapes.
