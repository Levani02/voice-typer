# 006 — Rebuilding the device list before every take

**Date:** 2026-08-20
**Status:** Accepted, unverified on hardware — the report is from a Mac this machine does not have

## Context

The first working macOS session produced a reproducible complaint: with AirPods in and
speaking into them, the app reported **„მიკროფონი ვერ ჩაირთო"** and recorded nothing.
Without them it worked.

`Recorder._open_stream` already retries at the device's own sample rate, which is what a
MacBook's built-in microphone needs. This is a different failure and the retry cannot
reach it: PortAudio enumerates the machine's audio devices once, inside `Pa_Initialize`,
and never looks again. `sounddevice` calls that at import. Anything paired afterwards —
AirPods being the ordinary case, because people put them on when they are about to talk —
does not exist as far as the running process is concerned.

Two failures follow from the same cause, and the quiet one is worse:

* macOS makes the newly connected headset the default input. PortAudio hands out the
  index that was default at launch. If that device is gone, opening it fails, which is
  the error the user saw.
* If it is still there — the laptop's own microphone usually is — the take opens
  **that** instead, and records the room while the user speaks into their ears. Nothing
  reports an error; the transcript is simply of the wrong sound.

## Decision

`recorder.refresh_devices()` restarts PortAudio (`sd._terminate()` then `sd._initialize()`)
and `Recorder.start()` calls it once, at the beginning of every take.

Placement is the whole decision. It runs **between** takes and never mid-take:

* `start()` only — `resume()` must reopen the very device the take began on. Rebuilding
  the list under a paused take could hand back a different index for the same name, and
  the second half of a sentence would be recorded from a different microphone, at a
  different rate, and spliced onto the first.
* Never with a stream open. Terminating PortAudio pulls the device out from under a
  recording in progress.

The failure is not fatal: a list that cannot be rebuilt is the stale list that was in use
until now, so it is logged as a warning and the take proceeds.

`app.py` also drops its cached microphone name at the start of each take. The footer used
to look it up once per run; now that the device can change between takes, a name that is
never asked for again is a name that is wrong.

## Alternatives rejected

**Query the device list without restarting PortAudio.** There is no such call. PortAudio's
CoreAudio backend builds its table at initialisation, and the documented remedy — the one
its own maintainers give — is to terminate and initialise again.

**Refresh only after a failed open.** It fixes the error the user saw and leaves the silent
one untouched: recording the room instead of the headset never raises anything to retry on.

**Ask the user to pick a device in `config.json`.** Moves the problem rather than solving
it. An index recorded in a file is exactly the thing that changes when headphones connect.

**Restart PortAudio on a timer, or on every device change.** More moving parts, and it
would have to be right about when a stream is open. Once per take is the smallest place
that is safe by construction.

## What is not known

The exact PortAudio error behind the message was never captured — the report came as a
sentence, not a log line. The reasoning above is the one explanation that fits both a
hard failure with headphones and no failure without them, and the fix is harmless if it
turns out to be something else. If **„მიკროფონი ვერ ჩაირთო"** appears with AirPods again,
`logs/voice_typer.log` now names the microphone that was actually opened, which is the
line that settles it.
