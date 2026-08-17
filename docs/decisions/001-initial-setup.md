# 001 — Project initialized with /setup

**Date:** 2026-08-17
**Status:** Accepted

## Context

`C:\Users\user\Desktop\Voice Transcription Tool` contained nothing but an empty `logs\`
directory and was not a git repository. Work was about to start on a Windows tray app for
Georgian dictation, and the user does not write code — every safeguard has to be in place
before the first line is written, not retrofitted after something goes wrong.

## Decision

Run `/setup` first, before any application code.

That establishes, in one pass:

- a git repository with `main` and `develop`, so "go back to when it worked" becomes a real
  option instead of a hope
- `.claude/rules/` — six rule files describing how Claude must behave in this project:
  plain-language reporting, no unrequested refactoring, no secrets in logs, mandatory
  manual verification for anything the automated suite cannot reach
- `.gitignore` covering `.env`, `*.wav`, and `logs/` from the very first commit, before any
  key or recording exists to leak
- `.github/` — CI on `windows-latest` (the app needs a real Windows environment to import
  its dependencies at all), dependency auditing, and secret scanning
- `docs/` with this decision record

## Alternatives rejected

**Write the app first, add infrastructure later.** The two things most worth protecting —
the API key and the user's recorded voice — both come into existence the moment the app
first runs. A `.gitignore` written afterwards protects nothing that already leaked.

**Skip git because the folder is outside a repository.** The parent `Desktop` folder is not
a repository, so without `git init` here the only fallback would be Claude's automatic file
backups, which cover only files Claude itself edited. That is a much weaker guarantee than
a commit.

## Consequences

- Checkpoints work in this folder from now on
- CI runs on Windows rather than Linux, which costs more runner minutes but is the only way
  `pynput` and `sounddevice` import successfully
- The rule files are project-specific rather than copied wholesale — the browser and
  viewport checks from the global UI rules do not apply to a tray app, so
  `.claude/rules/ui-verification.md` was rewritten around desktop screenshots instead
