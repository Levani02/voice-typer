# Code Quality Rules

## Size limits

- Functions under 50 lines
- Files under 400 lines
- Nesting depth 4 or less
- One primary concept per module — `recorder.py` records, it does not transcribe

## Resource cleanup — the rule that matters most here

This app holds three things that break the user's machine if leaked: an open microphone
stream, a global keyboard hook, and the clipboard contents.

- Every `sounddevice` stream is closed on **every** path — success, cancel, exception,
  and shutdown. Use `try/finally` or a context manager, never a bare `stream.stop()` at
  the end of a happy path.
- The `pynput` listener is stopped when the app exits, including on `Ctrl+C` and on tray Quit.
- Saved clipboard contents are restored in a `finally`, not after the paste call.

## Configuration

Zero hardcoded settings. The hotkey, the hold threshold, the maximum recording length,
the audio device, the sample rate, and the price-per-hour used for cost accounting all
live in `config.json`. A magic number in a module is a bug.

## Immutability and state

- The state machine has exactly one owner: `app.py`. No other module mutates it.
- Config is loaded once, validated, and treated as read-only afterwards
  (`@dataclass(frozen=True)`).
- Audio chunks accumulate in a list and are joined once at the end — never a growing
  `bytes` object rebuilt per callback.

## Error handling

- Every boundary handles its own errors: microphone open, API call, clipboard access, paste
- Errors carry context: `"microphone open failed: device 3 not available"`, not `"failed"`
- Log where the error is handled, not where it is raised
- Never swallow an exception silently. Never `except: pass`

## Naming

- Booleans read as questions: `is_recording`, `has_api_key`, `should_restore_clipboard`
- Functions are verb + noun: `start_recording`, `build_wav`, `inject_text`
- Constants SCREAMING_SNAKE_CASE, and only for values that are genuinely constant —
  anything the user might want to change belongs in `config.json` instead

## Formatting and linting

`ruff` for both. Zero errors before a commit.

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format .
```

## Git

- Branches: `main` (working), `develop` (integration), `feature/...` for new work
- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`
- Checkpoint commits use the `CHECKPOINT:` format from `~/.claude/rules/01-checkpoints.md`
- Never commit `.env`, a `.wav`, or anything under `logs/`

## Before calling anything done

- [ ] No function over 50 lines, no file over 400
- [ ] Every stream, listener, and clipboard save cleaned up on the error path
- [ ] No hardcoded timings or device names
- [ ] No `print()` left in module code — logging only
- [ ] The API key appears in exactly one place: `os.environ` via `config.py`
- [ ] Tests pass
