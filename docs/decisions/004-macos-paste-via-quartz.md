# 004 — On macOS the paste is posted to Quartz, not sent through pynput

**Date:** 2026-08-19
**Status:** Accepted, verified on macOS 15.6 (Apple Silicon)

## Context

On macOS 15.6 the application died the instant a transcript came back — nine crash
reports out of nine attempts. The log ended at `transcribed 4.3s into 55 characters` and
never reached `pasted 55 characters`. macOS reported `EXC_BREAKPOINT (SIGTRAP)` with this
at the top of the faulting thread:

```
libdispatch   _dispatch_assert_queue_fail
HIToolbox     islGetInputSourceListWithAdditions
HIToolbox     TSMGetInputSourceProperty
_ctypes       PyCFuncPtr_call
Python        gen_iternext / builtin_next
```

Constructing `pynput.keyboard.Controller` calls `get_unicode_to_keycode_map()`, which
enters pynput's `keycode_context()` generator — hence `builtin_next` / `gen_iternext` —
and that generator asks Carbon for the current keyboard layout through
`TISGetInputSourceProperty`. On macOS 15 that API asserts it is running on the main
queue. The paste runs on a worker thread by design, so the assertion fails, and a failed
`dispatch_assert_queue` does not raise a Python exception: it kills the process.

This is a threading-rule violation, not a permissions problem. It happened whether or not
Accessibility permission had been granted.

## Decision

`injector.py` posts the four key events itself on macOS — Command down, `v` down, `v` up,
Command up — through `CGEventCreateKeyboardEvent` / `CGEventPost`, with
`kCGEventFlagMaskCommand` set on the events that need it. `CGEventPost` is thread-safe and
touches no Text Input Source API. Windows keeps the pynput path unchanged.

The layout lookup that crashes was never buying anything: the key code is `kVK_ANSI_V`
(`0x09`), decided in ADR 003 precisely so that no layout is ever consulted.

The same change adds an `AXIsProcessTrusted()` check before posting. Without Accessibility
permission `CGEventPost` succeeds and delivers nothing — the app would have counted the
paste as done, restored the old clipboard over the transcript and deleted the recording.
A silent success is worse than an error, so a missing permission is now reported as a
failed keystroke, which leaves the text on the clipboard for the user to press Cmd+V.

## Alternatives rejected

**`osascript -e 'tell application "System Events" to keystroke "v" using command down'`**
— the fix suggested in the bug report. It works, but it spawns a process per paste,
costs 100 ms or more, and needs a *second* permission: Automation, controlling System
Events. That is another dialog to grant and another way for the paste to fail silently.

**Hopping to the main thread, e.g. through Tk's `after()`.** It would keep pynput in the
path, but the main thread is drawing the window; a paste queued behind a redraw is a paste
that arrives late. It also leaves the crash one refactor away from returning — anything
that constructs a `Controller` off the main thread brings it back.

**Pinning pynput to an older version.** The API being asserted against is macOS's, not
pynput's. No version of pynput avoids the layout lookup in its constructor.

## Consequences

- The macOS and Windows paste paths no longer share an implementation, only an interface.
  `_send_paste()` picks between them; tests drive both.
- Nothing on macOS constructs `pynput.keyboard.Controller`. The hotkey `Listener` still
  enters `keycode_context()` on its own thread, and has not been observed to crash there —
  if it ever does, the fix is the same shape: keep that call off the worker threads.
- Without Accessibility permission the user now sees "press Cmd+V" on every dictation
  instead of a crash. That is the honest state of the machine, not a regression.
