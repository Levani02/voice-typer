"""The ElevenLabs call, with the SDK replaced by a fake.

No test in this file ever reaches the network, so the suite costs nothing to run.
"""

from types import SimpleNamespace

import pytest

from voice_typer import transcriber as transcriber_module
from voice_typer.transcriber import Transcriber, TranscriptionError

GEORGIAN_SAMPLE = "გამარჯობა, ეს არის ტესტი"
WAV_BYTES = b"RIFF....WAVEfake"


class FakeSpeechToText:
    def __init__(self, responses, billed_seconds=None):
        self._responses = list(responses)
        self._billed_seconds = billed_seconds
        self.calls = []

    def convert(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if self._billed_seconds is None:
            return SimpleNamespace(text=outcome)
        return SimpleNamespace(text=outcome, audio_duration_secs=self._billed_seconds)


class FakeClient:
    def __init__(self, responses, billed_seconds=None):
        self.speech_to_text = FakeSpeechToText(responses, billed_seconds)


def make_transcriber(monkeypatch, responses, keyterms=(), billed_seconds=None):
    """Build a Transcriber whose client is the fake, with the retry pause removed."""
    client = FakeClient(responses, billed_seconds)
    monkeypatch.setattr(transcriber_module, "ElevenLabs", lambda api_key: client)
    monkeypatch.setattr(transcriber_module, "RETRY_DELAY_SECONDS", 0)
    return Transcriber("test-key-not-real", "scribe_v2", "kat", keyterms), client


def http_error(status: int, message: str | None = None) -> Exception:
    error = Exception(f"HTTP {status}")
    error.status_code = status
    if message is not None:
        error.body = {"detail": {"message": message}}
    return error


def test_returns_the_transcript(monkeypatch):
    transcriber, _ = make_transcriber(monkeypatch, [GEORGIAN_SAMPLE])
    assert transcriber.transcribe(WAV_BYTES).text == GEORGIAN_SAMPLE


def test_georgian_is_forced_rather_than_auto_detected(monkeypatch):
    """Auto-detection on short Georgian clips is the main source of nonsense output."""
    transcriber, client = make_transcriber(monkeypatch, [GEORGIAN_SAMPLE])
    transcriber.transcribe(WAV_BYTES)
    sent = client.speech_to_text.calls[0]
    assert sent["language_code"] == "kat"
    assert sent["model_id"] == "scribe_v2"


def test_audio_event_tags_are_off(monkeypatch):
    """Otherwise "(laughs)" would be pasted into whatever the user is writing."""
    transcriber, client = make_transcriber(monkeypatch, [GEORGIAN_SAMPLE])
    transcriber.transcribe(WAV_BYTES)
    sent = client.speech_to_text.calls[0]
    assert sent["tag_audio_events"] is False
    assert sent["diarize"] is False


def test_surrounding_whitespace_is_trimmed(monkeypatch):
    transcriber, _ = make_transcriber(monkeypatch, ["  " + GEORGIAN_SAMPLE + "\n"])
    assert transcriber.transcribe(WAV_BYTES).text == GEORGIAN_SAMPLE


def test_a_connection_failure_is_retried_once_and_can_succeed(monkeypatch):
    transcriber, client = make_transcriber(
        monkeypatch, [ConnectionError("network unreachable"), GEORGIAN_SAMPLE]
    )
    assert transcriber.transcribe(WAV_BYTES).text == GEORGIAN_SAMPLE
    assert len(client.speech_to_text.calls) == 2


def test_it_gives_up_after_the_second_failure(monkeypatch):
    transcriber, client = make_transcriber(
        monkeypatch, [ConnectionError("down"), ConnectionError("still down")]
    )
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(WAV_BYTES)
    assert len(client.speech_to_text.calls) == 2


def test_a_server_fault_is_retried(monkeypatch):
    transcriber, client = make_transcriber(monkeypatch, [http_error(503), GEORGIAN_SAMPLE])
    assert transcriber.transcribe(WAV_BYTES).text == GEORGIAN_SAMPLE
    assert len(client.speech_to_text.calls) == 2


def test_a_rejected_key_is_not_retried_and_says_so(monkeypatch):
    """Retrying a bad key wastes the user's time — the message must point at the key."""
    transcriber, client = make_transcriber(monkeypatch, [http_error(401), GEORGIAN_SAMPLE])
    with pytest.raises(TranscriptionError, match="API key"):
        transcriber.transcribe(WAV_BYTES)
    assert len(client.speech_to_text.calls) == 1


def test_a_key_missing_the_permission_is_told_apart_from_a_wrong_key(monkeypatch):
    """Really happened during setup. "The key was rejected" sends the user to recreate a
    key that was fine — the actual fix is one toggle on the same key."""
    failure = http_error(
        401, "The API key you used is missing the permission speech_to_text to execute this"
    )
    transcriber, _ = make_transcriber(monkeypatch, [failure])

    with pytest.raises(TranscriptionError, match="Speech to Text permission"):
        transcriber.transcribe(WAV_BYTES)


def test_the_server_explanation_is_only_used_when_it_is_about_permissions(monkeypatch):
    failure = http_error(401, "Invalid API key")
    transcriber, _ = make_transcriber(monkeypatch, [failure])

    with pytest.raises(TranscriptionError, match="rejected the API key"):
        transcriber.transcribe(WAV_BYTES)


def test_rate_limiting_is_retried(monkeypatch):
    transcriber, client = make_transcriber(monkeypatch, [http_error(429), GEORGIAN_SAMPLE])
    assert transcriber.transcribe(WAV_BYTES).text == GEORGIAN_SAMPLE
    assert len(client.speech_to_text.calls) == 2


def test_an_empty_response_is_an_error_not_an_empty_paste(monkeypatch):
    """Pasting nothing would look like the app silently swallowed the user's words."""
    transcriber, _ = make_transcriber(monkeypatch, ["   "])
    with pytest.raises(TranscriptionError, match="no text"):
        transcriber.transcribe(WAV_BYTES)


@pytest.mark.parametrize(
    "failure",
    [http_error(401), http_error(500), ConnectionError("network down")],
    ids=["rejected-key", "server-fault", "no-network"],
)
def test_the_api_key_never_reaches_the_error_message(monkeypatch, failure):
    """Every branch of the explanation, not just the 401 one — the catch-all fallback is
    the branch most likely to carry raw SDK detail through."""
    secret = "sk_this_must_never_be_shown"
    client = FakeClient([failure, failure])
    monkeypatch.setattr(transcriber_module, "ElevenLabs", lambda api_key: client)
    monkeypatch.setattr(transcriber_module, "RETRY_DELAY_SECONDS", 0)

    with pytest.raises(TranscriptionError) as caught:
        Transcriber(secret, "scribe_v2", "kat").transcribe(WAV_BYTES)

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


def test_key_terms_are_sent_when_configured(monkeypatch):
    """The one lever available for Georgian accuracy — names the model would guess at."""
    terms = ("სოხუმი", "ElevenLabs")
    transcriber, client = make_transcriber(monkeypatch, [GEORGIAN_SAMPLE], keyterms=terms)
    transcriber.transcribe(WAV_BYTES)

    assert client.speech_to_text.calls[0]["keyterms"] == list(terms)


def test_no_key_terms_parameter_is_sent_when_none_are_configured(monkeypatch):
    transcriber, client = make_transcriber(monkeypatch, [GEORGIAN_SAMPLE])
    transcriber.transcribe(WAV_BYTES)

    assert "keyterms" not in client.speech_to_text.calls[0]


def test_key_terms_are_capped_before_they_reach_the_api(monkeypatch):
    """Past 100 terms ElevenLabs bills a 20-second minimum for every recording."""
    terms = tuple(f"term{i}" for i in range(150))
    transcriber, client = make_transcriber(monkeypatch, [GEORGIAN_SAMPLE], keyterms=terms)
    transcriber.transcribe(WAV_BYTES)

    assert len(client.speech_to_text.calls[0]["keyterms"]) == transcriber_module.MAX_KEYTERMS


def test_the_billed_duration_is_reported_when_the_response_carries_one(monkeypatch):
    transcriber, _ = make_transcriber(monkeypatch, [GEORGIAN_SAMPLE], billed_seconds=4.25)
    assert transcriber.transcribe(WAV_BYTES).billed_seconds == 4.25


def test_a_missing_billed_duration_is_none_rather_than_a_guess(monkeypatch):
    transcriber, _ = make_transcriber(monkeypatch, [GEORGIAN_SAMPLE])
    assert transcriber.transcribe(WAV_BYTES).billed_seconds is None


def test_a_nonsense_billed_duration_is_ignored(monkeypatch):
    transcriber, _ = make_transcriber(monkeypatch, [GEORGIAN_SAMPLE], billed_seconds="soon")
    assert transcriber.transcribe(WAV_BYTES).billed_seconds is None
