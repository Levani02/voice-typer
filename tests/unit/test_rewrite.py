"""The one place a model is allowed to touch the user's words.

Every test here asks the same question from a different side: **can this lose a
dictation?** The answer has to stay no, whatever the model does — refuse, hang, answer in
the wrong language, answer with something else entirely, or not answer at all.

No test reaches the network. The client is a stand-in, so the suite costs nothing to run.
"""

import logging

import pytest

from voice_typer.rewrite import (
    ALL_REASONS,
    BUILTIN_PROMPT,
    MAX_RATIO,
    MIN_RATIO_DEFAULT,
    NOTHING_CONDENSED_ABOVE,
    load_prompt,
    rewrite,
)

SPOKEN = (
    "ასე რომ ვფიქრობ ამ საიტის თემაზე, ჯერ ერთი ის მთავარი გვერდი უნდა გადავაკეთოთ, "
    "მერე ის ფასების გვერდი, იქ ძალიან ბევრი ტექსტია"
)
POLISHED = (
    "ვფიქრობ ამ საიტზე. პირველ რიგში, მთავარი გვერდი უნდა გადავაკეთოთ. "
    "შემდეგ ფასების გვერდი, სადაც ძალიან ბევრი ტექსტია."
)


class FakeGemini:
    """Answers with whatever the test decided, and records what it was asked."""

    def __init__(self, answer="", error=None):
        self.answer = answer
        self.error = error
        self.calls: list[dict] = []

    def generate(self, model, prompt, text, timeout_ms):
        self.calls.append(
            {"model": model, "prompt": prompt, "text": text, "timeout_ms": timeout_ms}
        )
        if self.error is not None:
            raise self.error
        return self.answer


def run(text=SPOKEN, answer=POLISHED, error=None, **kwargs):
    client = FakeGemini(answer=answer, error=error)
    options = {
        "api_key": "test-key-not-real",
        "model": "gemini-2.5-flash",
        "prompt": "გადაწერე",
        "timeout_ms": 7000,
        "min_chars": 10,
        "client_factory": lambda _key: client,
    }
    options.update(kwargs)
    return rewrite(text, **options), client


# --------------------------------------------------------------------- the happy path


def test_the_polished_text_is_what_gets_pasted():
    result, _ = run()
    assert result.text == POLISHED
    assert result.rewritten


def test_the_model_is_asked_with_the_instruction_and_the_words():
    _, client = run()
    assert client.calls[0]["prompt"] == "გადაწერე"
    assert client.calls[0]["text"] == SPOKEN
    assert client.calls[0]["model"] == "gemini-2.5-flash"


# ------------------------------------------------------- nothing may lose a dictation


def test_a_network_that_is_down_pastes_the_spoken_words():
    result, _ = run(error=OSError("no route to host"))
    assert result.text == SPOKEN
    assert not result.rewritten


def test_a_missing_key_pastes_the_spoken_words_without_asking_anything():
    result, client = run(api_key="")
    assert result.text == SPOKEN
    assert client.calls == []  # nothing was sent anywhere


def test_an_empty_answer_pastes_the_spoken_words():
    result, _ = run(answer="   ")
    assert result.text == SPOKEN


def test_an_answer_that_is_a_twentieth_of_the_original_is_refused():
    """Condensing is the point, so half the length is now welcome. A twentieth is not:
    that is a headline, or an answer to a different question."""
    result, _ = run(answer="საიტი.")
    assert result.text == SPOKEN
    assert not result.rewritten


def test_a_condensation_to_a_fifth_of_the_length_is_kept():
    """The regression test for this whole change. Under the old 0.4 floor a real
    dictation — a plan the speaker retracted mid-sentence — came back at 0.16 and was
    refused, so the mode pasted the ramble it was asked to tidy."""
    answer = "ა" * int(len(SPOKEN) * (MIN_RATIO_DEFAULT + 0.05))
    result, _ = run(answer=answer)
    assert result.text == answer
    assert result.rewritten


def test_an_answer_that_is_far_too_long_is_refused():
    result, _ = run(answer=POLISHED * 3)
    assert result.text == SPOKEN


def test_an_answer_at_the_edge_of_the_allowed_range_is_kept():
    """The guard exists to catch a different answer, not to punish a slightly longer one."""
    answer = "ა" * int(len(SPOKEN) * (MAX_RATIO - 0.1))
    result, _ = run(answer=answer)
    assert result.text == answer


def test_an_answer_in_english_is_refused():
    """Silent translation is documented behaviour for non-English dictation, and a
    transcript in the wrong alphabet is unusable rather than merely imperfect."""
    result, _ = run(
        answer=(
            "I am thinking about this website. First, the main page needs to be redone. "
            "Then the pricing page, where there is far too much text on it."
        )
    )
    assert result.text == SPOKEN
    assert not result.rewritten


def test_latin_words_inside_a_georgian_answer_are_fine():
    """A product name or a file extension is not a language change."""
    answer = "ვფიქრობ, რომ ეს საიტი Figma-ში დავხატოთ და შემდეგ PDF-ად გავიტანოთ ყველასთვის."
    result, _ = run(
        text="ვფიქრობ რომ ეს საიტი ფიგმაში დავხატოთ და მერე პდფ ად გავიტანოთ", answer=answer
    )
    assert result.text == answer


def test_a_short_utterance_is_never_sent_to_the_model():
    """Too little context to tell a fragment from a command — the shape that invents whole
    paragraphs out of a one-second recording."""
    result, client = run(text="კარგი", min_chars=40)
    assert result.text == "კარგი"
    assert client.calls == []


def test_an_empty_transcript_asks_nothing():
    result, client = run(text="")
    assert result.text == ""
    assert client.calls == []


# ------------------------------------------------------------------- the facts guard

# Two numbers and two topics, so a test can drop one topic and keep the other.
NUMBERED = (
    "ასე რომ შეხვედრა 15 იანვარს გვაქვს დანიშნული, და კიდევ, თანხა 450 ლარი "
    "უნდა გადავრიცხოთ იმავე კვირაში, ასე რომ ორივე უნდა გავაკეთოთ"
)


def test_a_number_that_was_never_spoken_is_refused():
    """Condensing only ever deletes. A number the speaker did not say can only have been
    invented, and 450 turned into 540 is caught here rather than by a survival rule."""
    result, _ = run(
        text=NUMBERED,
        answer="შეხვედრა 15 იანვარს გვაქვს და 540 ლარი უნდა გადავრიცხოთ იმავე კვირაში.",
    )
    assert result.text == NUMBERED
    assert not result.rewritten


def test_a_number_added_out_of_nowhere_is_refused():
    result, _ = run(
        text=NUMBERED,
        answer="შეხვედრა 15 იანვარს გვაქვს, 450 ლარი უნდა გადავრიცხოთ 2025 წელს.",
    )
    assert result.text == NUMBERED
    assert not result.rewritten


def test_a_number_that_left_with_the_topic_it_belonged_to_is_allowed():
    """The test that proves the guard does not fight the feature. A speaker who drops a
    plan drops its amount with it, and refusing that would refuse the retraction this
    mode exists to handle."""
    answer = "შეხვედრა 15 იანვარს გვაქვს დანიშნული."
    result, _ = run(text=NUMBERED, answer=answer)
    assert result.text == answer
    assert result.rewritten


def test_a_number_missing_from_an_answer_that_condensed_nothing_is_refused():
    """Nothing else was shortened, so the number did not leave with a retracted plan —
    it was simply lost."""
    answer = "ასე რომ შეხვედრა 15 იანვარს გვაქვს დანიშნული, და კიდევ, თანხაც "
    answer += "უნდა გადავრიცხოთ იმავე კვირაში, ასე რომ ორივე უნდა გავაკეთოთ."
    assert len(answer) >= len(NUMBERED) * NOTHING_CONDENSED_ABOVE  # the gate this tests
    result, _ = run(text=NUMBERED, answer=answer)
    assert result.text == NUMBERED
    assert not result.rewritten


def test_a_number_said_three_times_may_be_written_once():
    """A repetition removed is the mode working, not a fact lost."""
    text = "ორ საათზე, ორ საათზე შევხვდეთ, ხომ გითხარი, ორ საათზე, 2 საათზე ზუსტად"
    answer = "შევხვდეთ 2 საათზე."
    result, _ = run(text=text, answer=answer)
    assert result.text == answer


def test_a_leading_zero_is_not_a_different_number():
    """09:00 and 9 are the same time. The guard may forgive; it may never accuse."""
    text = "შეხვედრა 09 საათზე გვაქვს დანიშნული, ასე რომ ადრე უნდა გავიღვიძოთ, ხომ ხვდები"
    answer = "შეხვედრა 9 საათზეა, ადრე უნდა გავიღვიძოთ."
    result, _ = run(text=text, answer=answer)
    assert result.text == answer


def test_a_spoken_number_turned_into_digits_is_refused():
    """Whether Scribe writes Georgian numbers as words or as digits has never been
    measured — ADR 008 flagged it and it is still open. Until it is, a digit that was
    not in the transcript is treated as invented, because a wrong number reads as
    completely correct and a raw paste does not."""
    text = "ასე რომ თანხა ოთხას ორმოცდაათი ლარია და იანვრის თხუთმეტში უნდა გადავრიცხოთ"
    result, _ = run(text=text, answer="თანხა 450 ლარია და 15 იანვარს უნდა გადავრიცხოთ.")
    assert result.text == text
    assert not result.rewritten


def test_a_number_hiding_inside_a_longer_one_does_not_count_as_present():
    """`450 in "2450"` is true and meaningless — the guard compares tokens."""
    result, _ = run(
        text=NUMBERED,
        answer="შეხვედრა 15 იანვარს გვაქვს და 2450 ლარი უნდა გადავრიცხოთ იმავე კვირას.",
    )
    assert result.text == NUMBERED
    assert not result.rewritten


def test_a_transcript_without_numbers_is_never_troubled_by_the_guard():
    result, _ = run()
    assert result.rewritten


def test_the_refusal_never_writes_the_numbers_into_the_log(caplog):
    """An amount, a date, a phone number. SECURITY.md promises the log does not learn
    them, and a refusal is no reason to break that."""
    with caplog.at_level(logging.WARNING, logger="voice_typer.rewrite"):
        run(
            text=NUMBERED,
            answer="შეხვედრა 15 იანვარს გვაქვს და 540 ლარი უნდა გადავრიცხოთ იმავე კვირას.",
        )
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "540" not in logged
    assert "450" not in logged


# ------------------------------------------------------------- the packaging it adds


@pytest.mark.parametrize(
    "answer",
    [
        f'"{POLISHED}"',
        f"„{POLISHED}“",
        f"```\n{POLISHED}\n```",
        f"```markdown\n{POLISHED}\n```",
    ],
)
def test_quotes_and_code_fences_are_stripped_rather_than_pasted(answer):
    result, _ = run(answer=answer)
    assert result.text == POLISHED


# --------------------------------------------------------------------- the instruction


def test_the_instruction_comes_from_the_file(tmp_path):
    path = tmp_path / "rewrite-prompt.md"
    path.write_text("გადაწერე ლამაზად", encoding="utf-8")
    assert load_prompt(path) == "გადაწერე ლამაზად"


def test_a_missing_instruction_file_falls_back_rather_than_failing(tmp_path):
    """Deleting the file must not take the mode down with it."""
    assert load_prompt(tmp_path / "absent.md") == BUILTIN_PROMPT


def test_an_empty_instruction_file_falls_back_too(tmp_path):
    path = tmp_path / "rewrite-prompt.md"
    path.write_text("   \n\n", encoding="utf-8")
    assert load_prompt(path) == BUILTIN_PROMPT


def test_the_reason_is_reported_without_the_words_in_it():
    """The reason reaches a notification and a log line. The transcript must reach
    neither."""
    result, _ = run(error=OSError("no route to host"))
    assert SPOKEN not in (result.fallback_reason or "")


def test_every_reason_is_one_of_the_named_constants():
    """`app.py` maps these to Georgian for the card. A reason invented at the raise site
    would arrive on screen in English."""
    for options in (
        {"api_key": ""},
        {"answer": "  "},
        {"answer": "საიტი."},
        {"error": OSError("no route to host")},
    ):
        result, _ = run(**options)
        assert result.fallback_reason in ALL_REASONS
