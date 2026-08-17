# Project: voice-typer

## Overview

A Windows tray application that turns spoken Georgian into typed text in whatever window
is focused. Press a key, speak, and the transcript is pasted at the cursor — in a Claude
chat, a browser field, Word, anywhere.

**Stack:** Python 3.13 · ElevenLabs Scribe v2 (speech-to-text) · `sounddevice` (microphone)
· `pynput` (global hotkey and paste) · `pystray` (tray icon).

**Why these choices:**
- **ElevenLabs Scribe v2** — the strongest Georgian transcription available. Local Whisper
  was rejected: this machine has no NVIDIA GPU, and Whisper's Georgian is weak.
- **Python 3.13, not 3.14** — the audio and input wheels this project needs do not ship for
  3.14 yet. The `python` on PATH is 3.14; the venv must be built from
  `C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe`.
- **Clipboard paste, not synthetic keystrokes** — Georgian characters cannot be typed
  reliably through simulated key events across keyboard layouts. Clipboard + `Ctrl+V` works
  in every application.

## Architecture

```
hotkey listener  →  recorder  →  ElevenLabs Scribe v2  →  text injector
      ↑                                                        │
      └────────────────  tray icon shows state  ───────────────┘
```

States: `IDLE → RECORDING → TRANSCRIBING → IDLE`, with `ERROR` falling back to `IDLE`.
`voice_typer/app.py` is the only module that knows about more than one other module.

See [docs/architecture.md](docs/architecture.md) for the module-by-module breakdown.

## How Claude Should Work With the User

- The user writes prompts, not code — explain everything in plain language, in Georgian
- Before making changes: say what will change and why
- After changes: explain how to verify — which key to press, what should appear on screen
- If a prompt is vague: ask clarifying questions BEFORE writing code
- Change ONLY what was asked — never refactor or "improve" uninstructed code
- If changing 4+ files: list them and get confirmation first
- Never show raw stack traces without a plain-language explanation first

## Data Safety

- Checkpoint before any multi-file change
- Never delete files without asking
- If the user says "undo" — restore with git

## Security Rules

- The ElevenLabs API key lives in `.env` only. Never in code, never in a log, never in a
  commit, never printed in a response — not even partially masked.
- `.env` is edited by the user alone. Claude supplies the line to paste, nothing more.
- Recorded audio is the user's own voice. Only the single most recent recording is kept
  on disk (`logs/last_recording.wav`), and only so a failed upload can be retried.
- Transcripts are not logged by default — only their character count. `LOG_TRANSCRIPTS=true`
  turns full logging on for debugging and should stay off otherwise.

Detailed rules: [.claude/rules/security.md](.claude/rules/security.md)

## Testing

- Audio, hotkey, and clipboard code cannot be fully unit-tested — it is verified by hand,
  with the user watching the screen. That is the primary gate.
- Everything that is pure logic — hold-vs-tap timing, config validation, usage accounting,
  WAV assembly — gets a unit test with a happy path and one error case.
- Network calls to ElevenLabs are mocked in tests. Tests never spend money.
- Before any commit: the suite must pass. Never `--no-verify`, never skip a test to go green.

Detailed rules: [.claude/rules/testing.md](.claude/rules/testing.md)

## Code Quality

- Functions under 50 lines, files under 400 lines, nesting depth 4 or less
- Every audio stream and every clipboard change is cleaned up on the error path too
- Zero hardcoded settings — hotkey, timings, device, and cost rate all live in `config.json`
- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`

Detailed rules: [.claude/rules/quality.md](.claude/rules/quality.md)

## Memory & Context

- End of session: record decisions worth keeping
- Architectural decisions: `docs/decisions/`

Detailed rules: [.claude/rules/memory.md](.claude/rules/memory.md)

## Commands

```powershell
# Create the virtual environment — note the explicit 3.13 interpreter
& "C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe" -m venv .venv

# Install dependencies
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Run the app
.venv\Scripts\pythonw.exe main.py

# Run the tests
.venv\Scripts\python.exe -m pytest
```
