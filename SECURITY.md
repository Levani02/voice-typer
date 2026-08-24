# Security Policy

## Reporting a vulnerability

Open a private security advisory on this repository, or email the maintainer directly.
Please do not open a public issue for a security problem.

Expected response: an acknowledgement within 3 days, and an assessment within 7 days.

## What tool handles

Two things worth protecting:

**Your ElevenLabs API key.** It lives in `.env`, which is excluded from git. It is never
written to a log and never printed. If it ever appears in a commit, treat it as
compromised — rotate it at elevenlabs.io rather than only deleting the commit, because the
value stays recoverable in git history.

**Your voice.** Recordings are sent to ElevenLabs for transcription; that is what the tool
does, and it is stated plainly in the README. Locally, each take is written to
`logs/pending/` just before its upload starts and deleted as soon as its text has landed in
a window — so a crash, a lost connection, or quitting mid-upload leaves something to
re-send, and a normal successful dictation leaves nothing behind. Files accumulate there
only for takes that never made it; the tray's Retry clears them one at a time.

Transcripts are not written to the log unless `LOG_TRANSCRIPTS=true` is set for debugging.
There is one exception: if the clipboard itself turns out to be unusable, the transcript is
written to `logs/last_transcript.txt` rather than discarded. Losing what someone just said
is the worse outcome, but it does mean that file can hold recent speech — it is overwritten
each time and excluded from git.

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

## Where the text goes

Audio goes to ElevenLabs. That is what the tool is for, and it has always been true.

In the **rewrite mode only**, the transcript — text, never audio — is also sent to Google's
Gemini API so that it can come back tidied. That mode is off by default, the card says when
it is on, and the words mode sends the text nowhere at all.

- `GEMINI_API_KEY` sits beside the ElevenLabs key under the same rules: never in code,
  never in a log, never in an error message, never echoed back in a reply.
- It is optional. Without it the rewrite mode pastes the raw transcript and says so.
- The clipboard is **never** sent as context. This app pastes through the clipboard, so
  including it would feed the model its own previous output — and whatever else the user
  happened to have copied.
- The instruction sent alongside the text is `rewrite-prompt.md`, in plain sight beside
  the settings file. Nothing is appended to it at runtime.
- `rewrite_prompt_file` names a file, not a path: no folders, no `..`, and it must end in
  `.md`. The window's menu opens that file, and a settings value must never be able to
  point a menu action at an arbitrary place on the disk.
