"""Dropping the hesitation sounds a transcript kept.

"ააა", "მმმ", "uh" — the noises someone makes while deciding what to say next. ElevenLabs
is asked not to write them down at all (`no_verbatim`), and this is the second net for the
ones that come back anyway.

Everything here is deliberately mechanical. A rule that cannot decide what a sentence
*means* also cannot decide to change it: a token is removed only when it is a hesitation
standing entirely on its own. That is the whole safety argument, and it is why this module
knows nothing about grammar, context, or intent.

Pure text in, pure text out. No logging — the caller is handed a count instead, because a
transcript must never reach a log file.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Punctuation that a hesitation may be wearing, and which leaves with it. A comma after
# "მმმ" is the model punctuating the hesitation, not the sentence.
#
# The closing mark is deliberately conditional on having opened one. A quote or bracket
# the hesitation did not open belongs to the user's sentence — swallowing it turns
# 'ის ამბობს "კარგი მმმ" ახლა' into 'ის ამბობს "კარგი ახლა', which no one can repair
# afterwards because the audio is already gone. When the marks do not pair up the
# hesitation simply survives, which is a blemish rather than a corruption.
_OPEN = r"(?P<open>[\"'«„“(\[{])?"
_TRAILING = r"[,;:]*"
_CLOSE = r"(?(open)[\"'»”)\]}]?)"
# A full stop, question or exclamation mark ends the *sentence*, so it is handed back and
# the tidy pass closes the gap: "ეს არის მმმ." becomes "ეს არის."
_SENTENCE_END = r"(?P<end>[.!?…]*)"

# What is left behind once tokens are cut out. Order matters: spacing first, then the
# punctuation that spacing exposed, then the edges.
_TIDY: tuple[tuple[re.Pattern[str], str], ...] = (
    # Newlines are left alone — a transcript with paragraphs keeps them.
    (re.compile(r"[ \t]{2,}"), " "),
    (re.compile(r"[ \t]+([,.!?;:…])"), r"\1"),
    (re.compile(r"(?:,[ \t]*){2,}"), ", "),
    (re.compile(r"^[\s,;:.!?…]+"), ""),
)


def strip_fillers(text: str, filler_words: tuple[str, ...]) -> tuple[str, int]:
    """Return `text` without standalone hesitation sounds, and how many went.

    An empty `filler_words` switches the whole thing off — the text is returned untouched
    without ever being scanned.
    """
    if not filler_words or not text:
        return text, 0

    removed = 0

    def cut(match: re.Match[str]) -> str:
        nonlocal removed
        removed += 1
        return match.group("end")

    stripped = _pattern(filler_words).sub(cut, text)
    if not removed:
        return text, 0

    for rule, replacement in _TIDY:
        stripped = rule.sub(replacement, stripped)
    return stripped.strip(), removed


@lru_cache(maxsize=8)
def _pattern(filler_words: tuple[str, ...]) -> re.Pattern[str]:
    """One pattern for the whole list, compiled once per configuration.

    Keyed on the tuple that comes straight out of the frozen `Config`, so the cache is
    safe and `strip_fillers` stays a pure function.
    """
    shapes = "|".join(_shape(word) for word in filler_words)
    # `(?<!\S)` and `(?!\S)` rather than `\b`: word boundaries are defined on word
    # characters, and Georgian sitting next to punctuation gives surprising answers.
    # These two say "nothing but whitespace on either side", which is what a standalone
    # token actually is — and it is what makes "მმართველი" safe from the filler "მმმ".
    return re.compile(rf"(?<!\S){_OPEN}(?:{shapes}){_TRAILING}{_CLOSE}{_SENTENCE_END}(?!\S)")


def _shape(word: str) -> str:
    """Turn a configured filler into the shape it matches, elongations included.

    Every letter must appear **at least as many times as it was written**, with no
    ceiling. So "ააა" matches "ააა" and "ააააა" but not "აა", and "hmm" matches "hmmmm"
    but not "hm".

    Requiring the full written length is what keeps real words out of reach. An earlier
    version let a run of three match a run of two, and "მმმ" therefore matched "მმ" — the
    Georgian abbreviation for millimetre — so a dictated "10 მმ" came out as "10". A
    number that has quietly lost its unit reads as completely correct, which is the worst
    kind of mistake this module can make. Anyone who really does hesitate in exactly two
    letters can add that spelling to the list.

    Only the first letter may also appear as a capital, and only if the alphabet has one.
    That covers a hesitation opening a sentence — "Uh, maybe" — without matching an
    acronym: "HMM" is a model, "ERM" is a department, and neither is anybody hesitating.
    """
    parts: list[str] = []
    index = 0
    while index < len(word):
        letter = word[index]
        run = 0
        while index < len(word) and word[index] == letter:
            run += 1
            index += 1
        atom = _first_letter_class(letter) if not parts else re.escape(letter)
        parts.append(f"{atom}{{{run},}}")
    return "".join(parts)


def _first_letter_class(letter: str) -> str:
    """The opening letter in either case, where the script has two of them."""
    upper = letter.upper()
    if upper == letter or len(upper) != 1:
        return re.escape(letter)
    return f"[{re.escape(letter)}{re.escape(upper)}]"
