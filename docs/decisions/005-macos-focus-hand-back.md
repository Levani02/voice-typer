# 005 — Giving the focus back on macOS, and refusing to paste without it

**Date:** 2026-08-19
**Status:** Accepted, unverified on hardware — see *What is not known* below

## Context

With the SIGTRAP crash of ADR 004 fixed, the first real macOS session surfaced the next
problem. Dictation started with the mouse — clicking the app's own record button — put
nothing into the target application. The user's own description was exact: *the caret
disappears, and macOS does not bring it back on its own.*

That is not a permission problem. `focus.py` did not exist; the hand-back lived in
`injector.py` and was Windows-only. `foreground_window()` returned 0 on any other system,
which switched the whole mechanism off. The design assumed the Mac window never takes the
focus in the first place, because `window_platform.py` asks Tk for a `help` window with
`noActivates`. The session showed that assumption is wrong.

The paste itself was not the only thing at stake. `inject_text` counts a keystroke that
was accepted as a paste that happened: it then restores the previous clipboard over the
transcript and `app.py` deletes the recording. A Cmd+V delivered into this app's own
canvas does nothing at all, silently — so the words would have been gone from the screen,
the clipboard and the disk, with `pasted N characters` in the log. That failure mode is
the same one the virtual-key-code comment in `injector.py` was written to prevent.

## Decision

**1. The focus code moves to `voice_typer/focus.py`, with an opaque token.**

Windows passes a window handle; macOS passes the process id of the frontmost application.
`app.py` keeps the watcher it already had and never learns the difference. `injector.py`
was at 367 lines of a 400-line limit, which settled where the new code goes.

**2. macOS reads the front application through `NSWorkspace.frontmostApplication` and
activates through `NSRunningApplication`, on the worker thread, never through
`NSApplication`.**

Both classes are documented as safe to use from any thread. `NSApplication` is not, and
`NSApp.yieldActivation(to:)` — the spelling Apple's own cooperative-activation article
leads with — would need a hop to the Tk main thread. `NSRunningApplication`'s
`activateFromApplication:options:` carries the same cooperative semantics without
touching `NSApplication`, so the hop is avoided and `injector.py` stays free of Tk.

**3. The activation options are `0`.**

`NSApplicationActivateIgnoringOtherApps` is deprecated as of macOS 14 and documented to
have no effect. Since macOS 14, activation is a request granted on user intent, and the
sanctioned case is exactly ours: the application holding the focus asks for it to go to
another. Passing the old flag would have produced a bouncing Dock icon and nothing else.

**4. Success is decided by re-reading the front application, not by the return value.**

Activation is asynchronous and returns before the target's window has the caret. So the
front application is polled every 20 ms for up to half a second, and only once it is no
longer this process does the paste go ahead, after a further 120 ms of settling.

**5. If the focus does not come back, nothing is pasted.**

`return_focus_to` returns False, `inject_text` raises `PasteFailedError`, and the user is
told the transcript is on the clipboard and to press Cmd+V. The recording stays on disk.
This is the one place where refusing to act is the safe option, because the alternative is
a silent success that destroys the text.

**6. "The focus is elsewhere" and "the focus cannot be seen" are different answers.**

Both come back as 0 from `foreground_window()`, and treating the second as the first is
how the safety mechanism would have destroyed the words it exists to protect: with AppKit
missing, or after a crash inside the frontmost read, the app would have concluded the user
was already somewhere else and pasted blind into its own canvas.

What is left to reason from is a fact the app already knows: pressing the hotkey never
moves the focus, and clicking the record button always does. `app.py` records which one
started the take and passes it down. When macOS cannot be asked where the focus is, the
hotkey path still pastes — it is the common one and it was never at risk — and the button
path refuses. Nothing is guessed.

**7. A crash probe, because an AppKit assertion is not an exception.**

`logs/.focus_probe-frontmost` and `logs/.focus_probe-activate` are created immediately
before their call and removed the moment it returns. If one is found at startup, the
previous run died inside that call and it is switched off — permanently, until the user
deletes the file, with the log saying which file that is. ADR 004 was written because a
macOS main-thread assertion killed the process without raising anything Python could
catch; these files are the only evidence that would survive a repeat, and they turn a
crash loop into a single crash followed by a degraded but working app.

One file per call, not one shared file. A shared one would have let the first frontmost
read of the next run delete the record of an activation that crashed — and the app would
have walked into the same crash again on every other launch.

**8. Windows behaviour is untouched.**

There the hand-back still pastes even when `SetForegroundWindow` was refused. That path is
verified on a real desktop, and a paste that lands somewhere beats one that lands nowhere.
macOS is stricter because there the refusal is the norm rather than the exception.

## Alternatives rejected

**`NSApp.yieldActivation(to:)` then `activate()`.** Apple's documented route, and it
carries an explicit guarantee that cooperative activation succeeds while the yielding app
is active. It needs `NSApplication` on the main thread, which means a Tk hop from a worker
thread — the exact shape of dependency that ADR 004 exists to keep out of this path. If
`activateFromApplication:options:` proves insufficient on hardware, this is the fallback
to revisit, deliberately and with the hop written down.

**`CGEventPostToPid(pid, event)`** — posting Cmd+V straight to the remembered process and
never touching the focus at all. Attractive, and thread-safe by the same argument as the
paste itself. Rejected because the evidence is contradictory: an inactive AppKit
application has no key window, so a menu key equivalent such as Cmd+V often has nowhere to
route. Apple's forums have it working only for the application that owns the menu bar;
other projects claim broader success. A path that silently loses text in some applications
is the worst of the four failure modes.

**Making the Tk window genuinely non-activating**, so nothing needs handing back. This is
the actual root cause and it remains open, deliberately. Tk's `noActivates` sets
`canBecomeKeyWindow` to NO but `kHelpWindowClass` has a style mask of 0, so Tk builds an
`NSWindow` rather than an `NSPanel` and the window server still activates the application
on click. The attribute that ORs in `NSWindowStyleMaskNonactivatingPanel` is
`nonActivating`, and it has to be applied before the window is realised — `overlay.py`
applies the current one after `root.update()`, so it is probably already a no-op. That fix
is worth making, but it changes window creation ordering and interacts with transparency
and `overrideredirect`; landing it beside this change would make a field failure
impossible to attribute. It gets its own commit and its own verification.

## What is not known

Nothing here was run on a Mac. Specifically:

- Whether `activateFromApplication:options:` is honoured on 15.6. Apple's guarantee is
  written around `yieldActivation`; that the `NSRunningApplication` spelling carries it too
  is inference. If it is refused, the poll turns that into an honest "press Cmd+V" — the
  cost of being wrong is a degraded feature, not lost words.
- Whether `NSRunningApplication` activation is genuinely abort-free off the main thread.
  The class documentation says the class is thread-safe; the precedent of
  `activateFileViewerSelectingURLs:` shows documentation is not a guarantee. The crash
  probe covers this, at a cost of one crash.
- Ad-hoc signing drops the Accessibility grant on every rebuild, so an update looks like a
  regression in the paste. That is a signing problem, not this one, and it is documented in
  the README instead.

The change was put through an adversarial review before shipping: four reviewers reading
the working tree, and every finding handed to a separate agent whose job was to refute it.
Twenty-one findings did not survive that; five did, and decisions 6 and 7 above are what
they turned into. Both were the same shape — a guard that protected the common case and
quietly opened the destructive one in a rare state.
