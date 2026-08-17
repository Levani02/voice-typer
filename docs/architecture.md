# Architecture

## What the app does

One background process. It watches for a key, records while the key is engaged, sends the
audio to ElevenLabs, and pastes the returned text into whatever window has focus.

```
hotkey listener  →  recorder  →  ElevenLabs Scribe v2  →  text injector
      ↑                                                        │
      └────────────────  tray icon shows state  ───────────────┘
```

## Modules

| File | Responsibility |
| --- | --- |
| `main.py` | entry point — builds the pieces, wires them together, handles shutdown |
| `voice_typer/config.py` | reads `.env` and `config.json`, validates both, fails fast |
| `voice_typer/recorder.py` | microphone capture into a 16 kHz mono WAV held in memory |
| `voice_typer/hotkey.py` | global key listener; tells hold apart from tap |
| `voice_typer/transcriber.py` | ElevenLabs Scribe v2 client, one retry, usage accounting |
| `voice_typer/injector.py` | clipboard save → set → `Ctrl+V` → restore |
| `voice_typer/tray.py` | tray icon, state colours, menu, notifications |
| `voice_typer/app.py` | the state machine that connects everything above |

`app.py` is the only module aware of more than one other module. Every other file can be
tested, or reasoned about, on its own.

## The state machine

```
IDLE ──key down──▶ RECORDING ──stop──▶ TRANSCRIBING ──text──▶ IDLE
                       │                      │
                       └──Esc: discard──▶ IDLE└──failure──▶ ERROR ──▶ IDLE
```

`ERROR` is a display state, not a dead end — the tray icon turns dark red, the reason goes
to the log, and the machine returns to `IDLE` ready for the next press.

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

## Where things are written

| Path | Contents |
| --- | --- |
| `logs/voice_typer.log` | rotating log, 1 MB × 3. No API key. No transcript unless `LOG_TRANSCRIPTS=true` |
| `logs/last_recording.wav` | the most recent recording, kept only so a failed upload can be retried |
| `logs/usage.json` | cumulative seconds and estimated cost, shown in the tray menu |

All three are excluded from git.

## External dependencies

One outbound host: `api.elevenlabs.io`. No listening port, no incoming connections, no
elevated privileges.

## Known limits

- Applications running as administrator will not accept a paste from this non-elevated
  process. Elevating the app to work around that would be worse than the limitation.
- The transcript sits on the clipboard for a fraction of a second, where other running
  programs could read it. Inherent to pasting into arbitrary applications.
- Transcription requires internet. Offline, the recording is saved for retry rather than lost.
