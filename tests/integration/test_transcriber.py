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
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def convert(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(text=outcome)


class FakeClient:
    def __init__(self, responses):
        self.speech_to_text = FakeSpeechToText(responses)


def make_transcriber(monkeypatch, responses):
    """Build a Transcriber whose client is the fake, with the retry pause removed."""
    client = FakeClient(responses)
    monkeypatch.setattr(transcriber_module, "ElevenLabs", lambda api_key: client)
    monkeypatch.setattr(transcriber_module, "RETRY_DELAY_SECONDS", 0)
    return Transcriber("test-key-not-real", "scribe_v2", "kat"), client


def http_error(status: int) -> Exception:
    error = Exception(f"HTTP {status}")
    error.status_code = status
    return error


def test_returns_the_transcript(monkeypatch):
    transcriber, _ = make_transcriber(monkeypatch, [GEORGIAN_SAMPLE])
    assert transcriber.transcribe(WAV_BYTES) == GEORGIAN_SAMPLE


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
    assert transcriber.transcribe(WAV_BYTES) == GEORGIAN_SAMPLE


def test_a_connection_failure_is_retried_once_and_can_succeed(monkeypatch):
    transcriber, client = make_transcriber(
        monkeypatch, [ConnectionError("network unreachable"), GEORGIAN_SAMPLE]
    )
    assert transcriber.transcribe(WAV_BYTES) == GEORGIAN_SAMPLE
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
    assert transcriber.transcribe(WAV_BYTES) == GEORGIAN_SAMPLE
    assert len(client.speech_to_text.calls) == 2


def test_a_rejected_key_is_not_retried_and_says_so(monkeypatch):
    """Retrying a bad key wastes the user's time — the message must point at the key."""
    transcriber, client = make_transcriber(monkeypatch, [http_error(401), GEORGIAN_SAMPLE])
    with pytest.raises(TranscriptionError, match="API key"):
        transcriber.transcribe(WAV_BYTES)
    assert len(client.speech_to_text.calls) == 1


def test_rate_limiting_is_retried(monkeypatch):
    transcriber, client = make_transcriber(monkeypatch, [http_error(429), GEORGIAN_SAMPLE])
    assert transcriber.transcribe(WAV_BYTES) == GEORGIAN_SAMPLE
    assert len(client.speech_to_text.calls) == 2


def test_an_empty_response_is_an_error_not_an_empty_paste(monkeypatch):
    """Pasting nothing would look like the app silently swallowed the user's words."""
    transcriber, _ = make_transcriber(monkeypatch, ["   "])
    with pytest.raises(TranscriptionError, match="no text"):
        transcriber.transcribe(WAV_BYTES)


def test_the_api_key_never_reaches_the_error_message(monkeypatch):
    secret = "sk_this_must_never_be_shown"
    client = FakeClient([http_error(401), http_error(401)])
    monkeypatch.setattr(transcriber_module, "ElevenLabs", lambda api_key: client)
    monkeypatch.setattr(transcriber_module, "RETRY_DELAY_SECONDS", 0)

    with pytest.raises(TranscriptionError) as caught:
        Transcriber(secret, "scribe_v2", "kat").transcribe(WAV_BYTES)
    assert secret not in str(caught.value)
