# 009 — Gemini does the rewriting, and only in one mode

**Date:** 2026-08-24
**Status:** Accepted; guards covered by tests, unverified against real Georgian speech
until the user runs it

## Context

ADR 008 shipped a second mode that pasted an instruction in front of the transcript and
left the work to whatever received it — a chat box with an assistant on the other side.
It cost nothing, added no latency, and needed no second vendor.

The user tried it and rejected it in one sentence: *"უკვე გასწორებული ტექსტი უნდა
ჩაჯდეს"* — the finished text should land at the cursor. Pasting an instruction and pressing
Enter is a different product from dictating.

That requires the app itself to send the text to a model. ElevenLabs cannot: its full API
surface was enumerated and there is no endpoint that takes text and returns text. The user
had earlier ruled out an Anthropic key; asked again with that constraint on the table, they
chose **Gemini 2.5 Flash** ($0.30 / $2.50 per million tokens — about half the price of
Claude Haiku 4.5; roughly $0.0013 for a 45-second dictation).

## Decision

**1. Gemini runs in the rewrite mode and nowhere else.**

The user's first instinct was to run it in both modes. It is in one, and that was their
call after the trade was laid out.

The words mode stays deterministic: `no_verbatim` and the filler list remove hesitations,
Scribe punctuates, and after ElevenLabs returns, **nothing changes the text**. Putting a
model there would mean every ordinary dictation passing through something that can swap a
word — the failure Wispr Flow's users reported as "Auto Cleanup too aggressive".

The consequence is the point: **the choice between the two modes is a real choice.** One
cannot alter what you said; the other can. That distinction would have evaporated if a
model sat in both.

**2. The instruction is a markdown file, read fresh on every take.**

`rewrite-prompt.md` sits beside `config.json` and the menu opens it. Tuning a prompt is a
loop of edit, speak, look — a restart in the middle of that loop is enough friction that
people stop bothering. Config is still loaded once and frozen; this file is content, not
configuration, and it is read where it is used.

`rewrite_prompt_file` names a file, not a path: no separators, no `..`, `.md` only. The
menu opens it, and this project's rule is that a menu action opens a fixed location rather
than one a settings file chose. Confining it to a bare filename in the settings folder
keeps that true while still letting the user keep several instructions and switch by name.

**3. Every failure pastes the words anyway.**

`rewrite()` never raises for an ordinary failure. No key, no network, a timeout, an empty
answer, an answer in the wrong alphabet, an answer far too long or far too short — each
returns the original transcript with a short reason, and the card says so in one line.

Silence would have been worse than the message: a missing key and a dropped network look
identical from the outside, and a user who is not told assumes the mode is broken.

The specific guards, each for a failure someone has already shipped:

| Guard | The failure it answers |
| --- | --- |
| 40-character floor | A Handy user fed a one-second silent recording through post-processing and got back a complete invented business email |
| length ratio 0.4–1.6 | The same user twice received text on an entirely different topic |
| script check | superwhisper documents non-English dictation returning as English |
| 7-second timeout, no retry | Somebody is watching a cursor. Polished text in 21 seconds is worth less than their own words now |
| clipboard never sent as context | superwhisper names clipboard context as a cause of hallucination — and this app *pastes through the clipboard*, so it would be feeding the model its own output |
| `<thinking>`-style wrappers stripped | A Handy user had reasoning blocks pasted inline |

**4. "Raw text to clipboard", in the menu.**

The way back from a rewrite the user did not want. It only reaches the clipboard — it does
not paste. By the time someone decides they preferred their own words, the caret may be in
a different window, and this app has already shipped two fixes for pasting into the wrong
one (ADR 004, 005).

**5. Self-corrections are resolved, not preserved.**

The instruction tells the model that when the speaker changed their mind mid-sentence, the
final decision stays and the abandoned half goes. This came from a real dictation the user
sent: a plan to buy a car, retracted three sentences later in favour of jeans.

This is the sharpest edge in the whole feature. The model decides which half to cut, and if
it decides wrongly the words are gone without a trace. It is defensible only because the
mode is opt-in, visible, and sits beside one that cannot do this.

## Alternatives rejected

**Anthropic.** Simpler for this project in one respect — it is the vendor the user already
talks to all day — and the design was written out in full before they declined it. Gemini
2.5 Flash is also about half the price.

**ElevenLabs Agents.** Investigated specifically to avoid a second vendor. Its LLM menu is
the same OpenAI/Anthropic/Google models, so the dependency is resold rather than removed;
it bills $0.003 per message **plus** the model's own tokens, roughly eight times the direct
cost; it needs a WebSocket session and an agent object per dictation; and the ElevenLabs key
would have to be widened from Speech-to-Text-only to ConvAI, so a key in a desktop settings
file could then spend on tokens. Worse on every axis including the one it was meant to fix.

**A local model.** No key, no cost, nothing leaves the machine. Rejected on Georgian:
small local models handle it poorly, and a rewrite that quietly garbles a low-resource
language is exactly the failure this design spends its effort avoiding.

**The instruction in `config.json`.** It was there first. A prompt that will be tuned wants
line breaks, headings and a worked example; a JSON string wants none of those.

## What is not known

* **How well Gemini handles Georgian on this task.** Never measured. The plan calls for 30
  real transcripts compared side by side before the mode is trusted, and the acceptance bar
  is zero meaning-changes out of thirty.
* **Whether the length and script guards are tuned right.** The numbers are reasoned, not
  measured. Too tight and good rewrites get refused; too loose and a wrong one gets through.
  The log records both lengths on every refusal, which is what would settle it.
