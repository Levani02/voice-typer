# Contributing

## Setup

```powershell
& "C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe" -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Then put your ElevenLabs API key into `.env`. Use Python 3.13 specifically — the audio and
input packages do not ship wheels for 3.14 yet.

## Workflow

1. Branch from `develop`: `git checkout -b feature/short-description`
2. Make the change
3. `ruff check .` and `ruff format .`
4. `pytest`
5. Test by hand what cannot be automated — microphone, hotkey, paste into another app
6. Commit with a conventional message: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`
7. Open a pull request against `develop`

## What gets rejected

- An API key, a transcript, or a `.wav` in the diff
- A microphone stream or keyboard listener that is not closed on the error path
- A hardcoded timing, device name, or hotkey — those belong in `config.json`
- A test deleted or skipped to make the suite pass
- A function over 50 lines or a file over 400

## Testing what cannot be unit-tested

Microphone capture, the global hotkey, and pasting into another application need a human
watching the screen. When a change touches those, describe in the pull request exactly what
you pressed and what appeared, and attach a screenshot.

Automated tests never call ElevenLabs for real — the client is mocked, so the suite costs
nothing to run.
