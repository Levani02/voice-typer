"""Dropping hesitation sounds — the one piece of this app that edits the user's words.

Every test here is really the same question asked from a different angle: can this delete
something the user meant to say? The answers have to stay no, which is why the rule is
whole-token only and why a single letter can never match.
"""

import pytest

from voice_typer.fillers import strip_fillers

GEORGIAN = ("ააა", "ეეე", "მმმ", "ჰმმ")
MIXED = (*GEORGIAN, "uh", "um", "hmm")


def test_a_filler_between_two_real_words_leaves_no_double_space():
    text, removed = strip_fillers("დღეს ააა სამსახურში წავედი", GEORGIAN)
    assert text == "დღეს სამსახურში წავედი"
    assert removed == 1


def test_an_elongated_filler_goes_however_many_letters_it_has():
    text, _ = strip_fillers("მე მმმმმმმ არ ვიცი", GEORGIAN)
    assert text == "მე არ ვიცი"


def test_a_shortened_filler_still_matches_because_the_letter_is_doubled():
    text, _ = strip_fillers("მე მმ არ ვიცი", GEORGIAN)
    assert text == "მე არ ვიცი"


def test_a_filler_that_carries_a_comma_takes_the_comma_with_it():
    """A comma after a hesitation punctuates the hesitation, not the sentence."""
    text, _ = strip_fillers("მე, მმმ, არ ვიცი", GEORGIAN)
    assert text == "მე, არ ვიცი"


def test_a_full_stop_after_a_filler_stays_on_the_sentence():
    text, _ = strip_fillers("ეს ყველაფერია ააა.", GEORGIAN)
    assert text == "ეს ყველაფერია."


def test_a_latin_filler_goes_whatever_its_capitalisation():
    text, removed = strip_fillers("So Uh maybe UM later", MIXED)
    assert text == "So maybe later"
    assert removed == 2


def test_a_real_word_that_begins_with_the_same_doubled_letter_is_left_alone():
    """The test that proves the whole design: matching is whole-token, never inside one."""
    text, removed = strip_fillers("მმართველი მოვიდა", GEORGIAN)
    assert text == "მმართველი მოვიდა"
    assert removed == 0


def test_a_single_letter_is_never_removed_so_list_markers_survive():
    text, removed = strip_fillers("ა) პირველი პუნქტი", GEORGIAN)
    assert text == "ა) პირველი პუნქტი"
    assert removed == 0


def test_a_transcript_that_is_only_a_filler_comes_back_empty():
    text, removed = strip_fillers("მმმ", GEORGIAN)
    assert text == ""
    assert removed == 1


def test_a_transcript_that_is_only_a_filler_and_a_full_stop_comes_back_empty():
    text, _ = strip_fillers("მმმ.", GEORGIAN)
    assert text == ""


def test_two_fillers_in_a_row_do_not_leave_a_double_space():
    text, removed = strip_fillers("მე ააა მმმ არ ვიცი", GEORGIAN)
    assert text == "მე არ ვიცი"
    assert removed == 2


def test_a_filler_on_each_side_of_a_comma_does_not_leave_a_double_comma():
    text, _ = strip_fillers("ააა, მმმ, კარგი", GEORGIAN)
    assert text == "კარგი"


def test_a_filler_at_the_very_start_leaves_no_leading_space():
    text, _ = strip_fillers("ააა დიახ", GEORGIAN)
    assert text == "დიახ"


def test_a_line_break_between_sentences_survives_the_cleanup():
    text, _ = strip_fillers("პირველი ააა წინადადება.\nმეორე წინადადება.", GEORGIAN)
    assert text == "პირველი წინადადება.\nმეორე წინადადება."


def test_nothing_is_touched_when_no_filler_words_are_configured():
    original = "მმმ ააა uh"
    text, removed = strip_fillers(original, ())
    assert text is original
    assert removed == 0


def test_a_transcript_without_fillers_comes_back_exactly_as_it_arrived():
    original = "ეს სუფთა წინადადებაა, ცვლილების გარეშე."
    text, removed = strip_fillers(original, MIXED)
    assert text is original
    assert removed == 0


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ('"მმმ" კარგი', "კარგი"),  # quoted hesitation goes with its quotes
        ("(ააა) კარგი", "კარგი"),
        ("კარგი — მმმ", "კარგი —"),
    ],
)
def test_punctuation_around_a_filler_goes_with_it(spoken, expected):
    text, _ = strip_fillers(spoken, GEORGIAN)
    assert text == expected


def test_the_count_reports_every_removal():
    _, removed = strip_fillers("ააა ერთი მმმ ორი ჰმმ სამი", GEORGIAN)
    assert removed == 3
