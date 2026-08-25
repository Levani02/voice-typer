# Architecture

## What the app does

One background process. It watches for a key, records while the key is engaged, sends the
audio to ElevenLabs, and pastes the returned text into whatever window has focus.

```
hotkey / card  →  recorder  →  ElevenLabs Scribe v2  →  [rewrite mode: Gemini]  →  injector
      ↑                                                                              │
      └──────────────  the card, and the tray icon, show the state  ─────────────────┘
```

In **words** mode nothing stands between Scribe and the cursor. In **rewrite** mode the
transcript — text, never audio — goes to Gemini and comes back condensed. See
[decisions/008](decisions/008-two-modes-and-no-second-vendor.md) and
[decisions/009](decisions/009-gemini-for-the-rewrite-mode.md).

## Modules

| File | Responsibility |
| --- | --- |
| `main.py` | entry point — builds the pieces, wires them together, handles shutdown |
| `voice_typer/app.py` | the state machine that connects everything else |
| `voice_typer/config.py` | reads the key file and `config.json`, validates both, fails fast |
| `voice_typer/recorder.py` | microphone capture into a 16 kHz mono WAV held in memory |
| `voice_typer/hotkey.py` | global key listener; tells hold apart from tap |
| `voice_typer/transcriber.py` | ElevenLabs Scribe v2 client, one retry, usage accounting |
| `voice_typer/rewrite.py` | Gemini, in the rewrite mode only; never raises for an ordinary failure |
| `voice_typer/fillers.py` | removes hesitation sounds from a transcript |
| `voice_typer/takes.py` | the recordings kept on disk between speaking and seeing the words |
| `voice_typer/injector.py` | clipboard save → set → paste → restore |
| `voice_typer/focus.py` | remembers the window the user was typing in, and gives it back |
| `voice_typer/single_instance.py` | refuses to start a second copy |
| `voice_typer/tray.py` | tray icon, kept as a fallback for the card |
| `voice_typer/first_run.py` | asks for the two API keys, and offers a desktop shortcut |
| `voice_typer/desktop_shortcut.py` | writes the shortcut, per platform |

### The card

The recorder window is six files, because one was 1115 lines against a 400-line limit.
The split is described in [decisions/011](decisions/011-splitting-the-card.md).

| File | Responsibility |
| --- | --- |
| `voice_typer/overlay.py` | the window itself: root, canvas, pointer, folding, refresh loop. **Makes no drawing call at all.** |
| `voice_typer/card_painter.py` | draws the card's shell, status row, meter and footer; hands back a record of everything it made |
| `voice_typer/card_buttons.py` | draws the controls and switches them between lit, hovered and greyed out |
| `voice_typer/card_layout.py` | measurements, colours and the `Card` record. No tkinter — testable without a screen |
| `voice_typer/card_menu.py` | the right-click menu: building it, relabelling it, showing it |
| `voice_typer/card_glyphs.py` | four pure glyph-drawing functions |
| `voice_typer/widget_theme.py` | colours, colour arithmetic, and the canvas drawing Tk lacks |
| `voice_typer/window_state.py` | where the card was left, and whether that place still exists |

### Per-platform

| File | Responsibility |
| --- | --- |
| `voice_typer/platform_support.py` | same shape on both systems, different implementation |
| `voice_typer/window_platform.py` | the three things Tk cannot express: never taking focus, real transparency, the true desktop extent |
| `voice_typer/tcl_paths.py` | points Tcl at the base Python install, so Tk can start from inside a venv on Windows |

`app.py` is the module that knows the most about the others — it imports ten, and it is
the only one that owns the state machine. `main.py` imports seven, and `overlay.py` eight;
everything below those three imports at most four, and nine modules import nothing local
at all. There are no import cycles: two edges (`config` → `hotkey`, `card_menu` →
`first_run`) are deferred to function scope on purpose.

## The state machine

```
IDLE ──key down──▶ RECORDING ──stop──▶ TRANSCRIBING ──text──▶ IDLE
                       │                      │
                       └──Esc: discard──▶ IDLE└──failure──▶ ERROR ──▶ IDLE
```

`ERROR` is a display state, not a dead end — the card and the tray icon both turn red, the
reason goes to the log, and the machine returns to `IDLE` ready for the next press. There
is also `PAUSED`, reachable from `RECORDING` and back, and `disabled` when the hotkey has
been switched off from the menu.

## The one key, two behaviours

A single key (F9 by default) carries both interaction styles, decided by how long it is held:

- **key down** → start recording immediately
- **release after 400 ms or more** → push-to-talk: stop and transcribe
- **release under 400 ms** → toggle: keep recording; the next short press stops it
- **Esc while recording** → discard, no upload, no cost

Recording stops on its own at 300 seconds so a stuck key cannot run up a bill.

## Audio

16 kHz, mono, 16-bit PCM — the smallest format that loses nothing for speech, which keeps
the upload fast. Chunks accumulate in a list during capture and are joined once at the end;
a WAV header is added in memory with the standard-library `wave` module. Nothing touches
the disk unless an upload fails.

## Why the text goes through the clipboard

Georgian characters cannot be delivered reliably by synthesising keystrokes — the result
depends on the active keyboard layout, and Georgian layouts map the same physical keys
differently. Putting the text on the clipboard and sending `Ctrl+V` bypasses layout
entirely and works in every application that accepts paste.

The cost is that the previous clipboard contents must be saved and put back, including when
the paste fails. That restore lives in a `finally` block.

### The paste key is a virtual-key code, not the letter "v"

`injector.py` sends `KeyCode.from_vk(0x56)`, never `"v"`. This is the single most important
line in the file, and it is not a style choice.

pynput resolves a *character* through `VkKeyScanW` against the calling thread's active
keyboard layout. The Georgian layout has no Latin "v" — measured on this machine,
`VkKeyScanExW('v', 0x0437)` returns `-1`, while the US layout returns `86`. When the lookup
fails, pynput falls back to `KEYEVENTF_UNICODE`, which Windows delivers as `VK_PACKET` with
`wVk = 0`. `Ctrl` + `VK_PACKET` matches no paste accelerator anywhere.

The failure would have been silent and destructive: `SendInput` still succeeds, so the app
would conclude it had pasted, delete the recording, and 300 ms later restore the previous
clipboard over the transcript. The words would be gone from the screen, the clipboard, and
the disk, with `pasted N characters` in the log.

`0x56` is VK_V — the same physical key on every layout. `Key.ctrl` and `Key.f9` were always
virtual-key based, so only the "v" was ever wrong. The same trap applies to `_same_key` in
`hotkey.py` if the hotkey is ever changed from `f9` to a single letter.

### On macOS the events are posted to Quartz, and pynput is not involved

Creating `pynput.keyboard.Controller` asks Carbon for the current keyboard layout, and on
macOS 15 that call asserts it is on the main queue. The paste runs on a worker thread, so
the assertion failed and took the whole process down with `SIGTRAP` — every transcript,
without exception. `injector.py` therefore posts Command down, `v` down, `v` up, Command
up itself through `CGEventPost`, which is thread-safe and consults no layout.

Before posting, `AXIsProcessTrusted()` is checked. Without Accessibility permission macOS
accepts the event and delivers it nowhere, and believing that success would restore the
old clipboard over the transcript and delete the recording. A missing permission is
reported as a refused keystroke instead, which leaves the text on the clipboard.

The full reasoning, and the alternatives that were rejected, are in
[decisions/004-macos-paste-via-quartz.md](decisions/004-macos-paste-via-quartz.md).

### Giving the focus back, and refusing to paste without it

Pressing the hotkey leaves the focus alone. Clicking the app's own record button does not:
the window comes forward, the caret in the other application goes away, and a paste sent
then would land on a canvas with nowhere to put it. `focus.py` remembers where the user
was — a window handle on Windows, the frontmost application's process id on macOS — and
puts it back before the keystroke.

The two systems differ in what happens when that fails. On Windows the paste goes ahead
anyway: the behaviour is verified on a real desktop and a paste that lands somewhere beats
one that lands nowhere. On macOS it does not. Activation there is a request the system may
refuse, and a Cmd+V into this app's own window would do nothing while still counting as a
success — which would restore the previous clipboard over the transcript and delete the
recording. So macOS waits for the focus to actually leave, and if it has not left within
half a second, nothing is pasted at all and the user is told to press Cmd+V.

The full reasoning is in
[decisions/005-macos-focus-hand-back.md](decisions/005-macos-focus-hand-back.md).

## The window is drawn, not laid out

Tk offers no gradients, no rounded corners, no shadows and no alpha, and the design has
all four. So the card is painted on a Canvas by `widget_theme`:

- **gradients** — one horizontal line per row, its colour interpolated between two ends
- **rounded corners** — each row is inset by how far the arc has come in at that height,
  so a stack of lines forms a rounded rectangle
- **translucency** — blended against the colour underneath before drawing, since there is
  nothing to be translucent over at draw time
- **the window's own corners** — Windows removes every pixel of one exact colour from the
  window (`-transparentcolor`), so what sits outside the card's curve is the desktop
  rather than a black box

Buttons are canvas drawings with hit-testing, not widgets: that is what allows the
gradient, the hover lift, and the disabled state to look like one designed surface.

Three constraints learned the hard way, all now covered by tests:

- **One Tk root per process.** Creating a second — even after destroying the first —
  fails. The window tests share a single window for that reason.
- **Consolas has no Georgian.** Tk substitutes a wider font for those runs, so a line
  sized against the monospace metrics overflows. The footer text is kept short for it.
- **A borderless window that is hidden and shown again can come back unmapped**, so it is
  never withdrawn; it is created visible and stays that way.

## Where things are written

| Path | Contents |
| --- | --- |
| `logs/voice_typer.log` | rotating log, 1 MB × 3. No API key. No transcript unless `LOG_TRANSCRIPTS=true` |
| `logs/pending/take-NNNN.wav` | one file per take, written before its upload and deleted once its text lands |
| `logs/last_transcript.txt` | only written when the clipboard is unusable, so the words are not lost |
| `logs/usage.json` | cumulative seconds and estimated cost, shown in the card's menu |
| `logs/window.json` | where the card was left, folded or open, and which mode |

All of them are excluded from git. On Windows they sit beside the executable; on macOS
under `~/Library/Application Support/voice-typer`, because a `.app` bundle is meant to be
read-only. `config.py` decides which, and the key file and `rewrite-prompt.md` follow.

## Losing nothing

Two rules, both of which exist because the alternative is the user saying something twice:

**Every take gets its own file, written before its upload.** Whatever kills the worker — a
crash, the power going out, Quit clicked while the icon is amber — leaves a file the tray
menu can re-send. Each file is deleted only after *its own* text has been pasted.

One shared file would not survive ordinary use: starting a second dictation while the first
is still uploading is a normal thing to do, and the second take would overwrite the first
take's safety net, which the first take would then delete on its way out. Hence
`take-0001.wav`, `take-0002.wav`, numbered on from whatever a previous session left behind.

Retry re-sends the newest file that no running job owns, so it can never duplicate work
already in flight. Quit waits up to 5 seconds for a transcription in progress — a courtesy,
not a safety net, since the audio is already on disk.

**Nothing slow runs on the keyboard hook thread.** `_stop_recording` is called from inside
a global low-level Windows keyboard hook, and every keystroke on the machine queues behind
it. A 300-second recording is about 9 MB; writing that inline would stall typing
system-wide, and a slow enough write can make Windows drop the hook entirely — which would
kill the hotkey until the app restarts. The write happens on the worker instead.

**The icon is computed, never asserted.** Four kinds of thread can change it — the hook,
two timers, and every worker. Each one calls `_settle_state()`, which looks at the recorder
and the job count and paints what is actually true. Recording outranks transcribing. A
worker finishing its job can no longer paint "ready" over a recording the user has since
started.

## Two ways a paste can fail

They need opposite advice, so they are separate exception types:

| Failure | What is true | What the user is told |
| --- | --- | --- |
| the clipboard write failed | the text is nowhere | it was saved to the logs folder |
| the keystroke was refused | the text is on the clipboard | press Ctrl+V (Cmd+V on macOS) |

## External dependencies

Two outbound hosts. `api.elevenlabs.io` receives the audio, on every take. Gemini receives
the **transcript, never the audio**, and only in the rewrite mode — in words mode nothing
is sent to it at all. No listening port, no incoming connections, no elevated privileges.

## Known limits

- Applications running as administrator will not accept a paste from this non-elevated
  process. Elevating the app to work around that would be worse than the limitation.
- The transcript sits on the clipboard for a fraction of a second, where other running
  programs could read it. Inherent to pasting into arbitrary applications.
- Transcription requires internet. Offline, the recording is saved for retry rather than lost.
