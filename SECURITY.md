# Security Policy

## Reporting a vulnerability

Open a private security advisory on this repository, or email the maintainer directly.
Please do not open a public issue for a security problem.

Expected response: an acknowledgement within 3 days, and an assessment within 7 days.

## What this tool handles

Two things worth protecting:

**Your ElevenLabs API key.** It lives in `.env`, which is excluded from git. It is never
written to a log and never printed. If it ever appears in a commit, treat it as
compromised — rotate it at elevenlabs.io rather than only deleting the commit, because the
value stays recoverable in git history.

**Your voice.** Recordings are sent to ElevenLabs for transcription; that is what the tool
does, and it is stated plainly in the README. Locally, only the single most recent
recording is kept (`logs/last_recording.wav`), and only so a failed upload can be retried.
Transcripts are not written to the log unless `LOG_TRANSCRIPTS=true` is set for debugging.
`*.wav` and `logs/` are excluded from git.

## Scope

The app runs with normal user privileges and never requests elevation. It does not open a
network port, does not accept incoming connections, and talks to exactly one external
host: `api.elevenlabs.io`.

## Known limitation, by design

The transcript passes through the Windows clipboard on its way into the focused
application. While it is there — a fraction of a second, after which the previous clipboard
contents are restored — any other running program can read it. This is inherent to pasting
text into arbitrary applications and is not something the app can prevent.
