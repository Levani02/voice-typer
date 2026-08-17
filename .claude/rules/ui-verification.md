# Visual Verification

This project has no web page and no browser. Playwright and viewport checks do not apply.
The visible surface is a Windows tray icon plus text landing in another application —
so "visual verification" here means screenshots of the desktop, and they are still mandatory.

## When to verify visually

| Change | What to capture |
| --- | --- |
| tray icon, colours, or menu (`tray.py`) | screenshot of the tray area in each state |
| anything in the paste path (`injector.py`, `app.py`) | screenshot of the text landing in Notepad |
| hotkey behaviour (`hotkey.py`) | screenshot or console capture showing HOLD vs TAP |
| a change the user described as "not working" | screenshot of the current behaviour **before** fixing |

## The check for text injection

1. Open Notepad and click into it so the cursor is visible
2. Hold **F9**, say one Georgian sentence, release
3. Screenshot the Notepad window

**Success:** the Georgian sentence sits at the cursor, correct script, no mojibake, no
leftover clipboard content pasted alongside it.

**Failure:** nothing appears · Latin characters or question marks instead of Georgian ·
the previous clipboard contents pasted instead of the transcript.

Repeat the same check in the Claude chat input — that is where the user actually wants it,
and rich text inputs behave differently from Notepad.

## The check for tray state

Capture the tray icon in all four states and confirm they are distinguishable at 16×16:

| State | Colour |
| --- | --- |
| idle | grey |
| recording | red |
| transcribing | amber |
| error | dark red |

At tray size, shape carries no information — colour has to do the work alone. If two states
look alike in the screenshot, the colours are wrong.

## Before and after

When changing something the user can see, capture both. Show them side by side and ask:

**ნახე შედეგი — კარგად გამოიყურება?**

## When the user sends a screenshot

Describe what is actually visible in it before proposing any fix. Do not assume which part
of the screen is the problem.
