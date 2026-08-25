# 011 — Splitting the card, and what stays too big on purpose

**Date:** 2026-08-25
**Status:** accepted

## The problem

`CLAUDE.md` sets a 400-line limit and its own "Known debt" paragraph recorded two files
breaking it: `overlay.py` at 1115 lines and `app.py` at 765. ADR 010 had already been
forced to pay for an addition by splitting `card_glyphs.py` out, and named the two seams
still available — the right-click menu and the window-position persistence — calling the
second *"the more valuable, because it would make five behaviours testable without a
desktop session."*

That sentence is the real complaint. The size was a symptom. Reading a file, deciding
whether a remembered corner is still on screen, and turning design pixels into screen
pixels are decisions that need no window, and while they sat inside `OverlayWindow` the
only way to exercise them was to build a real Tk root — which a build server cannot do.

## What was done

`overlay.py` became six files. The rule that decided every boundary: **a module that does
not need a window must not require one.**

| File | Lines | Needs a screen? |
| --- | --- | --- |
| `overlay.py` | 464 | yes — it owns the root, the canvas and the pointer |
| `card_painter.py` | 337 | yes |
| `card_buttons.py` | 287 | yes |
| `card_layout.py` | 203 | **no** — imports no tkinter |
| `card_menu.py` | 137 | partly — the labelling rules do not |
| `window_state.py` | 124 | **no** |
| `tcl_paths.py` | 41 | **no** |

`app.py` gave up `takes.py` (105 lines) — the recordings kept on disk between speaking and
seeing the words appear.

Three things came out of this that were not about size:

**The window no longer draws.** Not one canvas call is left in `overlay.py`. Painting
returns a record; the window holds it and hands back a `Frame` of already-decided values
each tick. Nothing in the drawing code has an opinion about what state the app is in.

**One record instead of eighteen fields.** Every id the canvas handed out used to be a
separate attribute, and `_rebuild` had to reset all of them by hand. A stale canvas id is
not an error in Tk — it is a silent no-op, so a forgotten line there produced a frozen
card and no exception. The record is now replaced whole in the same statement that clears
the canvas, which makes the mistake unavailable rather than merely discouraged.

**A load-bearing import order became a stated dependency.** `first_run.py` creates its own
Tk root, and on Windows that needs Tcl pointed at the base Python installation first. It
worked only because `main.py` imports the overlay — which did that as a side effect — a
few lines earlier. `tcl_paths.py` exists so both callers can say what they need.

Coverage went from 406 tests to 462. The 56 new ones all run without a desktop.

## What was deliberately not done

**`app.py` stays at 716 lines, over the limit.** What remains is the state machine, and
`.claude/rules/quality.md` says it has exactly one owner. The four methods that look
extractable — `_spawn_job`, `_start_worker`, `_finish_job`, `_claim_orphan` — share one
lock with `is_busy`, `shutdown` and `ui_state`. Splitting them would trade a size
violation for a concurrency bug. The limit is the wrong instrument here and the honest
record is that it is not met.

**`overlay.py` stays at 464 lines, over the limit.** What is left is the window: its
construction, pointer handling, folding, position, the refresh loop and its lifetime.
Every further cut considered produced a module that existed only to be small.

**Mixins were rejected.** Moving the code into classes that `OverlayWindow` inherits would
have brought every file under 400 with no test changes at all — genuinely cheaper. It was
refused because it would have left the coupling exactly as it was and made nothing newly
testable. The number would have been fixed and the problem would not.

**`tests/unit/test_overlay.py` (656 lines) stays whole.** Tk allows one root per process
and creating a second after the first is destroyed fails outright, which that file's own
comment records as something "these tests found the hard way". Splitting it would mean
either several root-creating fixtures, which fail, or changing how the whole suite starts,
for no coverage. It stays over the limit, said out loud here so nobody later "fixes" it.

**The `Controller` protocol stays in `overlay.py`.** It is this module's contract with the
app. A separate file for it would be imported by two modules and read by none.

## Alternatives considered

- **Stopping halfway** — extracting only the two seams `CLAUDE.md` named would have left
  `overlay.py` near 730 lines: still nearly twice the limit, with the debt less visible
  than before. Worse than either finishing or not starting.
- **One `card_painter.py`** — the drawing plus its per-frame updates is about 460 lines,
  so it would have replaced one oversized file with another.
- **A separate `card_meter.py`** — 46 lines whose one subtlety reads two fields of the
  same record. It would have existed to be small.
