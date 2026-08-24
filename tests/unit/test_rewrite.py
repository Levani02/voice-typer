"""The one place a model is allowed to touch the user's words.

Every test here asks the same question from a different side: **can this lose a
dictation?** The answer has to stay no, whatever the model does — refuse, hang, answer in
the wrong language, answer with something else entirely, or not answer at all.

No test reaches the network. The client is a stand-in, so the suite costs nothing to run.
"""

import pytest

from voice_typer.rewrite import (
    BUILTIN_PROMPT,
    MAX_RATIO,
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


def test_an_answer_that_is_far_too_short_is_refused():
    """Half the length is not a tidier sentence — it is a different one."""
    result, _ = run(answer="საიტი.")
    assert result.text == SPOKEN
    assert not result.rewritten


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
