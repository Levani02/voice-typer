# Memory and Context

## Session start

Read `CLAUDE.md` and the newest handoff note in `.claude/` if one exists. Do not recite
them back — summarise where things stood in one or two sentences and ask what to work on.

## Session end

1. Checkpoint any uncommitted work
2. Write a handoff note from `.claude/handoff-template.md`, named
   `.claude/handoff-YYYY-MM-DD.md`
3. Keep the newest 3 handoff notes, delete older ones
4. Run the Self-Extension Ritual from `~/.claude/CLAUDE.md` — ask what was learned, filter
   it, and **propose** where it belongs. Write nothing to global config without approval.

## Decisions

Architectural decisions go to `docs/decisions/NNN-title.md` — the choice, the alternatives
that were rejected, and why. The ones already recorded for this project:

- ElevenLabs Scribe v2 over local Whisper, over OpenAI, over Google
- Python 3.13 over the 3.14 that is on PATH
- Clipboard paste over synthetic keystrokes
- One key with dual behaviour over two separate hotkeys

If any of these is revisited, write a new ADR rather than editing the old one — the record
of what was tried has value.

## What does not belong in memory

Anything the repository already records: file structure, past fixes, git history, the
contents of `CLAUDE.md`. Anything that only matters inside the current conversation.

## Source of truth

Code beats memory; memory beats assumption. A memory reflects what was true when it was
written — if it names a file, a function, or a config key, confirm it still exists before
acting on it.

## Context window

Suggest `/compact` before 60% usage, `/clear` when the topic changes completely. Say it
plainly: "საუბარი გრძელდება. შევინახავ შეჯამებას, რომ არაფერი დაიკარგოს."
