"""Settings validation — a bad value must fail at startup, not during a recording."""

import json

import pytest

from voice_typer import config as config_module
from voice_typer.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch, tmp_path):
    """Keep the developer's real .env out of every test."""
    monkeypatch.setattr(config_module, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-not-real")
    monkeypatch.delenv("LOG_TRANSCRIPTS", raising=False)


def write_config(tmp_path, values):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_defaults_are_used_when_the_file_is_absent(tmp_path):
    cfg = load_config(tmp_path / "no-such-file.json")
    assert cfg.hotkey == "f9"
    assert cfg.hold_threshold_ms == 400
    assert cfg.language_code == "kat"


def test_values_from_the_file_win(tmp_path):
    cfg = load_config(write_config(tmp_path, {"hotkey": "F2", "hold_threshold_ms": 250}))
    assert cfg.hotkey == "f2"  # normalised to lower case
    assert cfg.hold_threshold_ms == 250
    assert cfg.max_recording_seconds == 300  # untouched default


def test_unknown_setting_is_rejected_by_name(tmp_path):
    path = write_config(tmp_path, {"hotkeys": "f9"})
    with pytest.raises(ConfigError, match="hotkeys"):
        load_config(path)


def test_malformed_json_names_the_line(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"hotkey": ', encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(path)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("hold_threshold_ms", 10),  # below the floor
        ("hold_threshold_ms", 99_999),  # above the ceiling
        ("max_recording_seconds", 0),
        ("max_recording_seconds", 99_999),  # a ceiling this high could run up a bill
        ("sample_rate", 100),
        ("price_per_hour_usd", -1),
        ("window_scale", 0),  # a window with no size at all
        ("window_scale", 0.1),  # too small for the Georgian labels to be read
        ("window_scale", 5),  # larger than most screens
        ("content_scale", 0.2),  # lettering smaller than the card it sits in can show
        ("content_scale", 3),  # labels would outgrow their buttons
    ],
)
def test_out_of_range_numbers_are_rejected(tmp_path, setting, value):
    with pytest.raises(ConfigError, match=setting):
        load_config(write_config(tmp_path, {setting: value}))


def test_window_scale_is_read_and_defaults_to_full_size(tmp_path):
    assert load_config(write_config(tmp_path, {"window_scale": 0.5})).window_scale == 0.5
    assert load_config(write_config(tmp_path, {})).window_scale == 1.0


def test_content_scale_is_read_and_defaults_to_matching_the_window(tmp_path):
    assert load_config(write_config(tmp_path, {"content_scale": 1.3})).content_scale == 1.3
    assert load_config(write_config(tmp_path, {})).content_scale == 1.0


def test_text_where_a_number_belongs_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="must be a number"):
        load_config(write_config(tmp_path, {"hold_threshold_ms": "fast"}))


def test_booleans_are_not_accepted_as_numbers(tmp_path):
    with pytest.raises(ConfigError, match="must be a number"):
        load_config(write_config(tmp_path, {"sample_rate": True}))


def test_empty_hotkey_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="hotkey"):
        load_config(write_config(tmp_path, {"hotkey": ""}))


def test_missing_api_key_explains_what_to_do(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "   ")
    with pytest.raises(ConfigError, match=r"\.env not found"):
        load_config(tmp_path / "no-such-file.json")


def test_the_key_never_appears_in_an_error_message(monkeypatch, tmp_path):
    """A key IS present here — the earlier version of this test deleted it first, which
    made the assertion impossible to fail."""
    secret = "sk_this_must_never_be_shown_to_anyone"
    monkeypatch.setenv("ELEVENLABS_API_KEY", secret)

    with pytest.raises(ConfigError) as caught:
        load_config(write_config(tmp_path, {"sample_rate": 3}))  # fails for another reason

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


def test_the_key_is_not_stored_anywhere_a_log_would_reach_it(monkeypatch, tmp_path):
    """Config objects get logged by accident; its repr must not carry the key."""
    secret = "sk_this_must_never_be_shown_to_anyone"
    monkeypatch.setenv("ELEVENLABS_API_KEY", secret)

    cfg = load_config(tmp_path / "absent.json")

    assert cfg.api_key == secret  # available to the code that needs it
    assert secret not in str(cfg.hotkey) + str(cfg.model_id) + str(cfg.language_code)


def test_keyterms_default_to_none(tmp_path):
    assert load_config(tmp_path / "absent.json").keyterms == ()


def test_keyterms_are_passed_through(tmp_path):
    cfg = load_config(write_config(tmp_path, {"keyterms": ["სოხუმი", "ElevenLabs"]}))
    assert cfg.keyterms == ("სოხუმი", "ElevenLabs")


def test_too_many_keyterms_is_rejected_because_it_would_cost_more(tmp_path):
    """Past 100 terms ElevenLabs bills a 20-second minimum per recording."""
    path = write_config(tmp_path, {"keyterms": [f"term{i}" for i in range(101)]})
    with pytest.raises(ConfigError, match="20-second"):
        load_config(path)


def test_exactly_one_hundred_keyterms_is_allowed(tmp_path):
    path = write_config(tmp_path, {"keyterms": [f"term{i}" for i in range(100)]})
    assert len(load_config(path).keyterms) == 100


def test_keyterms_must_be_a_list_of_words(tmp_path):
    with pytest.raises(ConfigError, match="keyterms"):
        load_config(write_config(tmp_path, {"keyterms": "სოხუმი"}))
    with pytest.raises(ConfigError, match="keyterms"):
        load_config(write_config(tmp_path, {"keyterms": [1, 2, 3]}))


def test_transcript_logging_is_off_unless_explicitly_enabled(monkeypatch, tmp_path):
    assert load_config(tmp_path / "absent.json").log_transcripts is False
    monkeypatch.setenv("LOG_TRANSCRIPTS", "TRUE")
    assert load_config(tmp_path / "absent.json").log_transcripts is True
    monkeypatch.setenv("LOG_TRANSCRIPTS", "yes")  # anything but "true" means off
    assert load_config(tmp_path / "absent.json").log_transcripts is False
