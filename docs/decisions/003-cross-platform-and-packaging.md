# 003 — macOS support, and shipping a standalone application

**Date:** 2026-08-18
**Status:** Accepted for Windows, unverified on macOS

## Context

The app was written for Windows alone and reached into Win32 in four places: the paste
path, the recorder window, the single-instance lock, and the three startup chores in
`main.py`. Installing it also meant installing Python 3.13 by hand, creating a virtual
environment, and pasting an API key into a file the user had to create first.

Two things were asked for: the same app on a Mac, and an installation that consists of
downloading one file and running it.

## Decisions

**1. Platform differences live in two modules, not in `if` statements scattered about.**

`platform_support.py` holds what has the same shape on both systems and a different
implementation underneath — opening a folder, showing a message before any window exists,
and the Windows-only DPI and console chores. `window_platform.py` holds the three things
Tk cannot express: never taking focus, real transparency, and the true extent of the
desktop.

Focus and paste behaviour deliberately stayed inside `injector.py`. The reasoning there —
why the paste key is addressed by hardware key code rather than by the letter "v" — only
makes sense next to the code it explains, and moving it would have left a comment
stranded from its subject.

**2. The paste key is a key code on both systems, for the same reason.**

`0x56` is VK_V on Windows; `0x09` is kVK_ANSI_V on macOS. Asking pynput for the character
"v" makes it consult the active keyboard layout, and the Georgian layout has no Latin
"v" — which on Windows silently produced a keystroke that pasted nothing while reporting
success. The same class of failure exists on macOS, so the same defence is used. The
modifier differs: Command on macOS, Control everywhere else.

**3. No tray icon on macOS.**

pystray's status-bar backend drives an AppKit run loop, and AppKit runs only on the main
thread — which Tk already owns. On Windows the tray gave way to the window for exactly the
same reason. Rather than contrive a way to share the main thread, the Mac build has no
tray at all. Nothing is lost: the recorder window is the status display on both systems,
and the original reason for the tray — Windows 11 hiding new icons behind the "^" arrow —
does not apply to macOS.

**4. The app asks for the API key itself, in a window, on first run.**

Previously a missing key was an error to report. It is now the one startup failure the app
repairs by asking: `MissingApiKeyError` is a separate exception, and `main` answers it with
`first_run.py` instead of a message box. The key is written to `.env` and into the
process environment, so the app carries on without a restart.

This is what makes a downloaded executable usable. Without it, the first run of a
single-file app would end in a message telling the user to create a file they have no
folder for.

**5. A packaged build keeps the user's files outside itself — differently per system.**

Windows: beside the `.exe`, which keeps the whole thing portable. macOS: under
`~/Library/Application Support/voice-typer`, because a `.app` bundle is replaced wholesale
on update and may be launched from a read-only image — writing the key inside it would
lose the key sooner or later.

**6. Both installers survive a half-finished first run.**

An `.env` whose key line is empty counts as *not configured*. The obvious check —
"does `.env` exist?" — would mean that a user who abandoned the first attempt is never
asked again, and gets a startup error instead every time.

## Alternatives rejected

**A Python-free installer that also installs Python** (via winget or a downloaded
installer). More moving parts, a longer first run, and it still leaves a Python on the
machine that the user did not ask for. A frozen build removes the dependency instead of
automating it.

**Wrapping `install.ps1` into an `.exe` with PS2EXE.** It would have produced a
double-clickable installer, but the installed result still needs Python 3.13 present. That
is the requirement the user wanted gone.

**Porting the overlay to a native macOS toolkit.** The window is 900 lines of Canvas
drawing that already works. `::tk::unsupported::MacWindowStyle` with `help` and
`noActivates` gives the same non-activating floating behaviour that `WS_EX_NOACTIVATE`
gives on Windows, so a rewrite bought nothing.

## What is verified, and what is not

Verified on Windows 11: the full suite passes, the source app is unchanged in behaviour,
`install.ps1` runs end to end, `voice-typer.exe` builds at 42 MB, and the first-run window
was photographed and corrected twice — the buttons were originally cut off by a window
100 px too short.

**Nothing on macOS has been run.** There is no Mac on the machine this was written on. The
Mac paths are written from the documented behaviour of Tk, pynput, pyperclip and PyInstaller,
and every one of them needs a real first run before the macOS support can be called more
than plausible. The specific things most likely to be wrong, in order:

1. `::tk::unsupported::MacWindowStyle` on an `overrideredirect` window — the two may
   conflict, in which case the window would take focus on every click
2. `-transparent` plus `systemTransparent` — if it fails the card loses its rounded
   corners, which is survivable and already falls back
3. Accessibility permission attaching to Terminal rather than to the app, when started
   from `run.command`
