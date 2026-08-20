# 008 — Two modes, and no second AI vendor

**Date:** 2026-08-20
**Status:** Accepted; verified on Windows with screenshots, unverified against a real
Georgian dictation until the user runs one

## Context

Three requests arrived together: hesitation sounds ("ააა", "მმმ") should not be typed,
spoken numbers should come out as digits, and — the interesting one — the user asked
whether the app should paste a **summary** of what they said rather than their words.

Eleven products were surveyed before answering: Wispr Flow, Willow Voice, superwhisper,
Aqua Voice, VoiceInk, FluidVoice, Handy, MacWhisper, TalkType, Dragon, Talon. Every claim
was checked against a primary source.

**Not one of them pastes a summary at the cursor.** Wispr Flow's help centre says its AI
layer "never shortens, summarizes, or condenses your speech". superwhisper's most capable
dictation mode names "Summarize this paragraph" as *incorrect* usage. Both ship
summarisation as a separate product for recorded meetings. The cleanup layer, by contrast,
is universal: fillers, punctuation, self-corrections, numbers.

The failures other people hit are specific and worth naming. A Handy user fed a
one-second silent recording through LLM post-processing and got back a complete invented
business email. The same user twice received text on an entirely different topic. A Willow
reviewer found the word "delete" being read as an editing instruction and the paragraph
rewritten, with no way to switch it off.

Then the user set a constraint of their own: **no Anthropic API in this project.**

## Decision

**1. Two modes on one key, switched by a control that is always visible.**

`F9` keeps doing one thing. What it does is decided by a mode the user sets beforehand:

* **words** (default) — the transcript, minus hesitations
* **summary** — the transcript with `summary_instruction` in front of it

The switch is a pill in the card's footer and an item in the right-click menu. It is
remembered in `logs/window.json` beside the window position and the folded state.

Not a second hotkey: `hotkey.py` compares one key and knows nothing about modifiers, and
`Shift+F9` is already taken in VS Code and Excel. Not a menu-only setting either — a mode
that changes what lands at the cursor has to be visible without opening anything.

**2. Summary mode asks no model anything.**

The app makes exactly one network call, the one it always made: audio to ElevenLabs. In
summary mode the instruction rides along in front of the words, and the summarising is
done by whatever receives the paste — the chat box the user was already typing into.

Cost: nothing. Added latency: nothing. Second key: none. New dependency: none.

The honest limit, stated in the README: it works where an LLM receives the paste. In Word
or Notepad the instruction is pasted too and has to be deleted. That is exactly why it is
a mode and not the default.

**3. Hesitation removal in two independent layers.**

`no_verbatim=true` asks ElevenLabs not to write them down. `filler_words` is a second net
on this side, matching **whole tokens only** — which is what makes "მმართველი" safe from
the filler "მმმ", and why config validation refuses any entry shorter than two characters.
Either layer can be switched off without the other.

**4. The folded card grows rather than hides the mode.**

Collapsed, the strip carries the mode pill — and only in summary mode, so anything the
folded strip says is worth reading. The window is wider in that mode by design.

## Alternatives rejected

**Calling the Anthropic API to clean and summarise.** Designed in full, then declined by
the user, who did not want a second vendor in the project. The design is still the right
one for "clean prose into a Word document", which is the one thing the shipped approach
cannot do; it is written down here so it does not have to be rediscovered.

**ElevenLabs Agents as the text processor.** Investigated specifically to keep one vendor.
Rejected on four counts: its LLM menu is the same OpenAI/Anthropic/Google models, so the
dependency is not removed, only resold; it bills $0.003 per message **plus** the model's
own tokens; it needs a stateful WebSocket session and an agent object per dictation, about
seven new moving parts where there was one; and the API key would have to be widened from
Speech-to-Text-only to ConvAI, so a key sitting in a desktop `.env` could spend on tokens.
That is a worse security position than the one being avoided.

**A local Georgian filler list only, with no `no_verbatim`.** A list cannot resolve a false
start — deciding which half of "ხვალ… არა, ზეგ" survives requires understanding the
sentence. The model can do that; a rule cannot, and this project does not write rules that
change meaning.

**Rules for punctuation and self-corrections on our side.** Same reason. Punctuation is
already handled by Scribe, and anything past that is judgement, not pattern-matching.

**Summarising by default, or as a level on a dial.** The whole survey says no, and the
Willow report shows the shape of the failure: rewriting-by-default with no off switch makes
ordinary words unspeakable. This user dictates *წაშალე*, *შეცვალე*, *შეაჯამე* into chat
boxes as content.

## What is not known

* **Whether `no_verbatim` catches Georgian hesitations.** The feature is documented without
  per-language promises, and every published example is English. The local list exists
  because of that gap.
* **Whether Scribe already writes Georgian numbers as digits.** Never measured. Until it is,
  no numeral conversion is built — a table that is 95% right produces silently wrong
  numbers, and a wrong number looks completely correct.
* **Georgian sits in ElevenLabs' second accuracy tier of four** ("High Accuracy, >5% to
  ≤10% WER"). `keyterms` is the documented lever for that and has been empty since the
  project started.
