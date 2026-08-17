# 002 — ElevenLabs Scribe v2 for Georgian transcription

**Date:** 2026-08-17
**Status:** Accepted, with a measurement still owed

## Context

The tool needs to turn spoken Georgian into text. Georgian is a low-resource language for
speech recognition, and the gap between engines is much wider than for English — so the
choice matters more here than it usually would.

## Decision

Use ElevenLabs Scribe v2, `language_code="kat"`, at $0.22 per hour of audio.

## What the evidence actually says

**No vendor publishes a measured Georgian word error rate, and no independent head-to-head
of commercial speech APIs on Georgian exists.** That is the honest state of the field, and
it should be stated plainly rather than papered over.

One specific trap is worth recording so nobody falls into it later. ElevenLabs' Georgian
landing page carries a "3.1% FLEURS / 5.5% Common Voice" figure. That is **not** a Georgian
number — it is their global 99-language average, and the identical sentence appears
verbatim on their Greek page. The real per-language table on that same page puts Scribe v1
Georgian at 10.9%. Their current documentation places Scribe **v2** Georgian in the
">5% to ≤10%" tier.

The only hard, citable Georgian numbers belong to open models:

| Model | Common Voice ka | FLEURS ka |
| --- | --- | --- |
| NVIDIA FastConformer-ka (115M, CC-BY-4.0) | 5.73% | 13.44% |
| SeamlessM4T-v2 | 11.14% | 9.79% |
| Whisper large-v3, zero-shot | 78.16% | 88.31% |

Source: arXiv:2501.14788, corroborated by arXiv:2604.08786, which additionally found that
smaller Whisper models emit Latin transliteration for Georgian at 0% script fidelity.

## Alternatives rejected

**Whisper, in any form — local, whisper.cpp, or hosted.** Not a close call. A 78–88% word
error rate is not a weaker option, it is an unusable one, and this machine has no NVIDIA
GPU to run it on anyway. This is the decisive reason local transcription was never viable
here, and it is worth remembering when the idea comes back around as "we could avoid the
API cost".

**AssemblyAI.** Self-declares Georgian at 25–50% WER and excludes it from their flagship model.

**Speechmatics, Mistral Voxtral.** No Georgian support at all.

**Google Chirp, Deepgram Nova-3, Azure, Gladia.** All list `ka`/`ka-GE`. None publishes a
Georgian accuracy figure. They remain plausible alternatives, not demonstrated better ones.

**NVIDIA FastConformer-ka.** The best *measured* Georgian result of anything found, free,
CPU-viable, and trained on exactly this domain — short single-speaker clips. Rejected for
now only because of the weight of the NeMo dependency on Windows. If the API cost or the
accuracy ever becomes a real problem, this is the first thing to try.

## What is still owed

The only way to get a true answer is a bake-off on the user's own voice: roughly 30 clips,
about 15 minutes of audio, single-digit cents through each API. Score with **CER, not
WER** — Georgian is agglutinative, and word error rate over-penalises a single wrong
morpheme in an otherwise correct word.

Until that is run, this decision rests on the absence of evidence against ElevenLabs
rather than on evidence for it. Nothing found justifies switching; nothing found proves the
choice is best.

## Consequences

- Audio leaves the machine. Stated in the README and in SECURITY.md.
- Roughly $0.22 per hour of speech, tracked in `logs/usage.json` and shown in the tray menu.
- `keyterms` in `config.json` is the one accuracy lever available without changing engine.
  Capped at 100 entries: above that ElevenLabs bills a 20-second minimum per request, which
  would cost several times more for a short dictation.
- The language is forced to `kat` rather than auto-detected. On short Georgian clips,
  detection is the largest single source of nonsense output.
