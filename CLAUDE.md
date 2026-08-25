# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Project: voice-typer

## Overview

A tray application for **Windows and macOS** that turns spoken Georgian into typed text in
whatever window is focused. Press a key, speak, and the transcript is pasted at the cursor —
in a Claude chat, a browser field, Word, anywhere.

**Stack:** Python 3.13 · ElevenLabs Scribe v2 (speech-to-text) · Gemini 2.5 Flash (rewrite
mode only) · `sounddevice` (microphone) · `pynput` (global hotkey) · Tk (the overlay window)
· `pystray` (tray icon) · `pyobjc` (macOS paste and window behaviour).

**Why these choices:**
- **ElevenLabs Scribe v2** — the strongest Georgian transcription available. Local Whisper
  was rejected: this machine has no NVIDIA GPU, and Whisper's Georgian is weak.
- **Python 3.13, not 3.14** — the audio and input wheels this project needs do not ship for
  3.14 yet. The `python` on PATH is 3.14; on Windows the venv must be built from
  `C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe`.
- **Clipboard paste, not synthetic keystrokes** — Georgian characters cannot be typed
  reliably through simulated key events across keyboard layouts. Clipboard + paste works
  in every application.
- **Gemini in one mode only** — see "The two modes" below. This is the load-bearing design
  decision of the whole feature, recorded in `docs/decisions/009-*.md`.

## Architecture

```
hotkey / overlay  →  recorder  →  ElevenLabs Scribe v2  →  [rewrite mode: Gemini]  →  injector
       ↑                                                                                │
       └──────────────  overlay card + tray icon show the state  ─────────────────────┘
```

States: `IDLE → RECORDING → TRANSCRIBING → IDLE`, with `ERROR` as a display state that
falls back to `IDLE`. **`voice_typer/app.py` is the only module that knows about more than
one other module** — every other file can be tested, or reasoned about, on its own. That is
the property to preserve when adding anything.

See [docs/architecture.md](docs/architecture.md) for the module-by-module breakdown and
[docs/decisions/](docs/decisions/) for why each hard choice was made.

### Things that are not obvious from any single file

- **The transcript is composed, not just returned.** `App._compose` is the fork between the
  two modes and the only place a model is allowed near the user's words.
- **Transcription runs off the UI thread.** Each take is written to
  `logs/pending/take-NNNN.wav` *before* its upload starts and deleted as soon as its own
  text lands in a window. A crash or a lost connection therefore leaves something to
  re-send; a successful dictation leaves nothing. Orphaned takes are claimed on startup.
  Never let two takes share one file — the later one would overwrite the earlier one's
  safety net.
- **Where the app's files live differs by platform.** `config._project_root()` returns the
  folder beside the executable on Windows and
  `~/Library/Application Support/voice-typer` on macOS, because a `.app` bundle is meant to
  be read-only. `config.json`, the key file, `rewrite-prompt.md` and `logs/` all follow it.
- **`rewrite-prompt.md` is content, not configuration.** It is read fresh on every take so
  the prompt can be tuned without restarting. `config.json` is loaded once and frozen.
  `rewrite_prompt_file` must be a bare `.md` filename — no separators, no `..` — because a
  menu action opens it, and a settings value must never aim a menu action at an arbitrary path.
- **The paste key on Windows is a virtual-key code, not the letter `"v"`.** Under a Georgian
  keyboard layout the character lookup fails and the paste silently does nothing.
  `injector.py` explains this at length; do not "simplify" it.

### The one key, two behaviours

A single key (F9 by default) carries both interaction styles, decided by how long it is held:
**hold ≥ 400 ms** is push-to-talk, **tap < 400 ms** toggles recording on and off, **Esc**
discards without uploading. Recording stops on its own at 300 seconds so a stuck key cannot
run up a bill.

### The two modes

| Mode | What happens after ElevenLabs returns |
| --- | --- |
| **სიტყვები** (words) | **Nothing.** `no_verbatim` and `filler_words` remove hesitation sounds, Scribe punctuates, and no model touches the text. |
| **გამართვა** (rewrite) | The transcript — text, never audio — goes to Gemini and comes back **condensed**: every topic the speaker raised survives, tightened, and what they retracted mid-sentence does not. |

Keeping the words mode deterministic is the point: it makes the choice between the two a
real choice, one that cannot alter what you said. **Do not add a model to the words mode.**

`rewrite()` never raises for an ordinary failure. No key, no network, a timeout, an answer
in the wrong alphabet, an answer far too long or short, or one carrying a number nobody
said — each returns the **original transcript** plus a short reason shown on the card. A
dictation can never be destroyed by the rewrite being unavailable.

The facts guard runs in one direction on purpose. Condensing only ever deletes, so
demanding that every spoken number survive would refuse the retraction the mode exists to
handle. Inventing is never legitimate, and catches alteration for free. The log records
counts, never the numbers themselves.

Reasons are English constants in `rewrite.py` so the log stays greppable; `app.py` owns
the Georgian the card shows. Every `_notify` now reaches the card as well as the tray —
on macOS, where there is no tray, those messages were previously seen by nobody.

## How Claude Should Work With the User

- The user writes prompts, not code — explain everything in plain language, in Georgian
- Before making changes: say what will change and why
- After changes: explain how to verify — which key to press, what should appear on screen
- If a prompt is vague: ask clarifying questions BEFORE writing code
- Change ONLY what was asked — never refactor or "improve" uninstructed code
- If changing 4+ files: list them and get confirmation first
- Never show raw stack traces without a plain-language explanation first

Detailed rules: [.claude/rules/interaction.md](.claude/rules/interaction.md)

## Data Safety

- Checkpoint before any multi-file change
- Never delete files without asking
- If the user says "undo" — restore with git

## Security Rules

- **Two keys, same rules.** `ELEVENLABS_API_KEY` and `GEMINI_API_KEY` live in the key file
  and nowhere else. Never in code, never in a log, never in a commit, never printed in a
  response — not even partially masked. The Gemini key is optional; without it the rewrite
  mode pastes the raw transcript and says so.
- **The app asks for both keys itself** — `first_run.py` shows two fields, ElevenLabs
  required and Gemini optional, and the same window reopens from the card's menu so an
  existing install can add the second key without editing a file. An empty field means
  "leave that one alone": saving one key must never disturb the other.
- Claude does not edit the key file. If a key has to be set outside the app, Claude
  supplies the line to paste and stops there.
- Recorded audio is the user's own voice. Takes live in `logs/pending/` only between the
  start of their upload and the moment their text lands in a window.
- Transcripts are not logged by default — only their character count. `LOG_TRANSCRIPTS=true`
  turns full logging on for debugging and should stay off otherwise.
- **The clipboard is never sent to the model as context.** This app pastes *through* the
  clipboard, so including it would feed the model its own previous output.

Detailed rules: [.claude/rules/security.md](.claude/rules/security.md) ·
[SECURITY.md](SECURITY.md)

## Testing

- Audio, hotkey, and clipboard code cannot be fully unit-tested — it is verified by hand,
  with the user watching the screen. That is the primary gate.
- Everything that is pure logic — hold-vs-tap timing, config validation, usage accounting,
  WAV assembly, filler removal, the rewrite guards — gets a unit test with a happy path and
  one error case.
- Network calls to ElevenLabs and Gemini are mocked in every automated test. Tests never
  spend money. `rewrite()` takes a `client_factory` argument for exactly this reason.
- **`make_config` in `tests/unit/test_app.py` must be updated whenever `Config` gains a
  field** — the dataclass is frozen and has no defaults, so a new setting breaks it.
- Before any commit: the suite must pass. Never `--no-verify`, never skip a test to go green.

Detailed rules: [.claude/rules/testing.md](.claude/rules/testing.md)

## Code Quality

- Functions under 50 lines, files under 400 lines, nesting depth 4 or less
- **Known debt, and the two files that keep it.** `app.py` is about 715 lines and
  `overlay.py` about 465, both against that 400-line limit. Neither is an oversight —
  `docs/decisions/011-*.md` records why each stays and what was tried. `app.py` is the
  state machine, which has one owner by rule; `overlay.py` is the window itself. Do not
  add to either without splitting something out, and do not "fix" the number by moving
  code into a mixin the same class inherits: that was considered and refused, because it
  changes no coupling and makes nothing newly testable.
- **The card is six files now** — `overlay.py` owns the root, the canvas, the pointer and
  the refresh loop and **makes no drawing call at all**; `card_painter.py` and
  `card_buttons.py` draw; `card_layout.py` holds the measurements, the colours and the
  `Card` record; `card_menu.py` the right-click menu; `window_state.py` where the card was
  left. Every id the canvas hands out lives in one `Card`, replaced whole whenever the
  canvas is cleared — a stale id is a silent no-op in Tk, so resetting fields by hand is
  how the card ends up frozen with nothing in the log.
- **`tests/unit/test_overlay.py` is over the limit and stays that way.** Tk allows one
  root per process; splitting the file would need several, which fails outright.
- Every audio stream and every clipboard change is cleaned up on the error path too
- Zero hardcoded settings — hotkey, timings, device, model names, thresholds and the cost
  rate all live in `config.json`, validated at startup, unknown keys rejected
- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`

Detailed rules: [.claude/rules/quality.md](.claude/rules/quality.md)

## Memory & Context

- End of session: record decisions worth keeping
- Architectural decisions: `docs/decisions/NNN-title.md` — a new ADR rather than an edit to
  an old one, so the record of what was tried survives

Detailed rules: [.claude/rules/memory.md](.claude/rules/memory.md)

## Commands

```powershell
# Create the virtual environment — note the explicit 3.13 interpreter
& "C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe" -m venv .venv

# Install dependencies
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Run the app (pythonw = no console window)
.venv\Scripts\pythonw.exe main.py

# Tests — all, one file, one test
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m pytest tests/unit/test_rewrite.py
.venv\Scripts\python.exe -m pytest -k "filler and quote"

# Lint and format — both must be clean before a commit
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format .

# Build the standalone app -> dist/voice-typer.exe
.venv\Scripts\python.exe tools\build_app.py
```

On macOS the same commands read `.venv/bin/python`, and the build produces
`dist/voice-typer.app`.

**PowerShell 5.1 has no `&&`** — chain with `;` and `if ($?)`, or use the Bash tool.

### Releasing

There is no Mac on the development machine, so **GitHub Actions is the only place a `.app`
can be built or proven to start.** The release flow:

1. Bump `version` in `pyproject.toml`
2. Commit, then tag `vX.Y.Z` and push the tag
3. `.github/workflows/build.yml` builds both platforms and publishes a release with both
   files attached

`gh workflow run build.yml --ref <branch>` builds without releasing, when only an artifact
is needed. A tag with a `-beta.N` suffix still triggers everything — mark the result with
`gh release edit <tag> --prerelease` so the stable version stays "Latest".

**After replacing the `.app` on macOS the Accessibility permission must be granted again** —
remove the entry with "−" and add it back with "+". The old entry looks present but no
longer works, and without it the hotkey does nothing at all.
