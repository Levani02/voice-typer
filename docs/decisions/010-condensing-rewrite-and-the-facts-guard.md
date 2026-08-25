# 010 — The rewrite condenses, and the facts guard replaces the length floor

**Date:** 2026-08-25
**Status:** Accepted; the guards are covered by tests, and the prompt is unverified
against real Georgian speech until the user runs it

## Context

ADR 008's revision of 2026-08-24 chose "polish" over three condensing candidates and put
*"ნუ შეაჯამებ და ნუ შეამოკლებ"* into the instruction. ADR 009 then gave the answer a
length floor of 0.4 and recorded, in its own "what is not known" section, that the number
was *"reasoned, not measured"* and that *"too tight and good rewrites get refused."*

Both of those held for about a day, and then the file started arguing with itself. A
"გადაფიქრება" section was added to `rewrite-prompt.md` telling the model to drop a
retracted plan and everything that depended on it — a rule whose own worked example is
27% of its input, and therefore a rule the 0.4 floor refuses. The user then dictated a
sentence in exactly that shape:

> ხვალ უნდა წავიდეთ ბანკში და გავხსნათ ანგარიში… მაგრამ იცი რა, ბანკი ჯერ არ მინდა.
> მოდი ჯერ ბინა ვნახოთ და მერე გადავწყვიტოთ.

226 characters in; the correct answer is 37, or 0.16. Refused, raw transcript pasted.

**And they could not see why.** The reason went to `tray.notify` and nowhere else.
Windows 11 files new tray icons behind the "^" arrow by default — which the app's own
`main.py` docstring already says, as the reason the window exists at all — and on macOS
`self._tray` is `None`, so every message this app has ever produced on that platform went
to nobody. A whole session was spent diagnosing a mode that was reporting itself
correctly into a channel with no reader.

The user's instruction, in their own words: *"ჩემი მიზანია რომ ჩემი ნასაუბრალიდან
დაწეროს გამართული ტექსტი შინაარსობრივად"*, and — about the list of cases — *"გადაფიქრების
წესი არის ძალიან კონკრეტული და ყველა შესაძლო შემთხვევას ვერ ვაქცევთ წესებად."*

## Decision

**1. The mode condenses. Every topic survives; length does not.** ADR 008's choice stands
for *topics* and is reversed for *length*. A speaker who talks for a minute and means
three sentences gets three sentences. Retracted material is not a topic: it goes, and so
does whatever only served it. Output is always flowing paragraphs — bullets were measured
in ADR 008 and flattened five topics to equal weight.

**2. One principle, not a list of cases.** `rewrite-prompt.md` now turns on a single
question — *ამ სიტყვებს რომ ამოვიღებ, დაიკარგება რამე, რისი თქმაც მოსაუბრეს მართლა
უნდოდა?* — which subsumes repetition, hesitation, false starts and retraction without
enumerating any of them. One worked example survives, because ADR 008 measured that the
example itself changes behaviour; it was rewritten to demonstrate compression, topic
survival, dependent removal and hedge preservation at once, and it explains why the
surviving topics survived.

**3. The floor drops to 0.15 and moves into `config.json`** as `rewrite_min_ratio`, range
0.05–0.9. It is the one guard whose right value depends on a file the user is expected to
edit: tune the prompt to squeeze harder and the floor has to follow. A number that must
move with `rewrite-prompt.md` but can only be changed by editing Python is in the wrong
place. `MAX_RATIO` stays in code — no prompt should ever want more words back than went
in — and so do the script and facts thresholds, which are correctness invariants rather
than preferences.

**4. A facts guard replaces what the floor was doing, and it runs in one direction.** The
obvious rule — every number spoken must survive — is the wrong one: condensing deletes,
so *"ორ საათზე შევხვდეთ… არა, სამზე"* legitimately loses the two, and the rule would
refuse exactly the rewrites this ADR exists to allow. Inventing is never legitimate. So:
**no number may appear in the answer that was not spoken.** That catches alteration for
free, because turning 450 into 540 means producing a 540 nobody said.

The survival half is kept, but gated on evidence rather than on a tolerance: only when the
answer is at least 75% of the original length — so nothing was condensed away — is a
missing number treated as a mistake. Numbers are compared as whole tokens (`450` does not
count as present inside `2450`), as sets (a number said three times may be written once),
and with leading zeros stripped (`09` and `9` are the same number). **The log records
counts, never the numbers themselves**: an amount, a date or a phone number is exactly the
content `SECURITY.md` promises stays out of the log.

Digits are the whole scope. Proper nouns are excluded for three reasons, not one: Georgian
has no capitalisation to separate a name from a noun, Georgian declines names so token
identity would need a stemmer, and `test_latin_words_inside_a_georgian_answer_are_fine`
already accepts `ფიგმაში` → `Figma-ში`, which a "no new Latin token" rule would refuse.

**5. Every notice reaches the card.** `_notify` writes to a `(message, timestamp)` pair
that the window polls through `ui_notice()`, alongside everything else it polls — no push
into Tk from a worker thread, and the message expires by itself. It lands on the footer
line, taking the space the microphone name occupies, because that is the least urgent
thing on the card and the only reason anyone reads that line is to find out why something
did not happen. Not the status row: an overlapping take can bring a message in while the
next recording is running, and hiding „იწერს" would make the card lie about what it is
doing.

Reasons stay English constants in `rewrite.py` and become Georgian in `app.py` — the same
split `_report_error` already makes, detail to the file and a sentence to the screen. A
test asserts the mapping is total, which free-form Georgian at the raise site could not
promise. The card gets the reason alone and the tray gets the whole sentence: measured at
the user's own `window_scale: 0.5` / `content_scale: 1.3`, the line beside the mode pill
is 165 pixels, the prefixed sentence needs 172, and every bare reason fits inside 99.

**6. `card_glyphs.py` pays for the addition.** `overlay.py` was 1114 lines against a
400-line limit and CLAUDE.md forbids growing it without a split. The drawn glyphs —
microphone, pause bars, cross, power ring — move out as plain functions. `widget_theme.py`
already documents this seam: keeping the arithmetic elsewhere "leaves `overlay.py` to
describe the layout rather than the mechanics."

## Alternatives rejected

**Requiring every spoken number to survive, unconditionally.** Refuses the retraction case
that ADR 009 §5 and this ADR both exist to allow.

**Comparing numbers as a multiset.** A number said three times and written once is the
mode working, not a fact lost.

**Skipping the facts check when the transcript has no digits.** It would kill the
words-to-digits false positive outright, but blind the guard in the case where fabrication
is worst: a number invented into a dictation that had none.

**Covering proper nouns or Latin tokens.** See decision 4.

**Lowering the floor and adding nothing.** The floor was the only thing standing between
the user and an answer on a different subject. Removing its strength without replacing its
job is how a guard becomes decoration.

**Making `rewrite_max_ratio` configurable too, for symmetry.** Symmetry is not a reason.
The ceiling guards against padding and invention; no prompt edit should move it.

**Putting the notice on the status row, or auto-unfolding the card.** Both make the card
misreport what it is doing.

**Creating Georgian reasons inside `rewrite.py`.** It would put Georgian in the log and
make the mapping impossible to prove total.

## What is not known

**Whether Scribe writes Georgian numbers as digits or as words.** ADR 008 flagged this and
it is still unmeasured. If Scribe writes „ოთხას ორმოცდაათი" and Gemini answers „450", the
facts guard fires on every dictation containing a number. Three things make that
survivable rather than fatal: the instruction states the rule explicitly, the log
distinguishes the two directions so one grep says which way it fired, and the fix is a
prompt edit with no restart. It is still the first thing to check on real speech.

**Whether 0.15 is right.** One dictation, not thirty. ADR 009's acceptance bar — zero
meaning changes across thirty real transcripts — is still unmet, and condensing raises the
stakes: the failure this mode can now produce is a paragraph that reads perfectly with a
topic missing from it.

**Whether one principle beats a list of cases.** It is the user's judgement and it is
sound, but only measurement settles it.

**Whether the model honours "flowing paragraphs".** The instruction forbids lists and
`_unwrap` strips fences and quotes, but nothing refuses an answer that comes back
bulleted. A "no leading dash" rule would refuse a legitimate dash, so none was added.

**Whether the notice line is legible at 0.5 / 1.3.** It was measured and screenshotted at
those settings during development, not read by the user on their own screen.
