# Security Rules

## The API key — absolute

- `ELEVENLABS_API_KEY` lives in `.env` and nowhere else
- Never hardcoded, never a default fallback in `config.py`, never in `config.json`
- Never logged, never printed, never echoed back in a response — not even partially masked
- Never in a URL query string. It goes in the `xi-api-key` header, which the SDK handles
- `.env` is edited by the user alone. Claude supplies the line to paste and stops there
- Check whether the key is set without revealing it:

```bash
python -c "import os,dotenv;dotenv.load_dotenv();print('KEY SET:', bool(os.environ.get('ELEVENLABS_API_KEY')))"
```

- If the key ever lands in a commit it is **compromised**. Say so plainly and tell the user
  to rotate it at elevenlabs.io — deleting the commit is not enough.

## The user's voice is sensitive data

- Recorded audio is speech from the user's own room. Treat it as private.
- Each take is written to `logs/pending/take-NNNN.wav` just before its upload and deleted
  as soon as its own text lands in a window, so a successful dictation leaves nothing
  behind and only interrupted ones remain. Never make several takes share one file — the
  later one overwrites the earlier one's safety net.
- Transcripts are **not** written to the log by default — only their character count.
  `LOG_TRANSCRIPTS=true` is a debugging switch and stays off otherwise.
- One deliberate exception: when the clipboard itself is unusable, the transcript goes to
  `logs/last_transcript.txt` rather than being discarded — losing what someone just said is
  the worse outcome. It is overwritten each time and excluded from git. Do not extend this
  to any other path without saying so plainly to the user.
- `*.wav` and `logs/` are in `.gitignore`. Never commit a recording.
- Audio is uploaded to ElevenLabs for transcription. That is the whole point of the tool,
  but it must be stated in the README so the user knows where their voice goes.

## Clipboard hygiene

- The transcript passes through the Windows clipboard. Whatever the user had copied before
  is saved and restored after a paste that worked.
- After a paste that **failed**, the transcript deliberately stays on the clipboard, so the
  user can press Ctrl+V themselves. Restoring it there would destroy the only copy.
- The two failure modes must never be conflated. `ClipboardUnavailableError` means the text
  never reached the clipboard and Ctrl+V would find nothing; `PasteFailedError` means it is
  there and Ctrl+V works. Telling the user the wrong one loses their words.

## Input and boundaries

- Validate `config.json` at startup: unknown key names, out-of-range timings, and a
  nonexistent audio device all fail fast with a plain-language message.
- Never `eval` config values. Never pass a config value to a shell.
- The tray "Open logs folder" action opens a fixed path, never a path from config.

## Errors

- A failed API call reports a generic sentence to the user plus the detail in the log file.
  No stack trace in a notification.
- HTTP error bodies from ElevenLabs may echo request data — log the status code and the
  message, never the raw request.

## Least privilege

- The app runs as the normal user. It must never ask for administrator rights.
- Consequence, stated in the README: applications running elevated will not accept the
  paste. That is correct behaviour, not a bug to work around by elevating.
