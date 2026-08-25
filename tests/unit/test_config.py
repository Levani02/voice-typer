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


# ------------------------------------------------------- hesitation sounds and modes


def test_no_verbatim_is_on_by_default(tmp_path):
    assert load_config(tmp_path / "absent.json").no_verbatim is True


def test_no_verbatim_can_be_switched_off(tmp_path):
    cfg = load_config(write_config(tmp_path, {"no_verbatim": False}))
    assert cfg.no_verbatim is False


def test_no_verbatim_must_be_true_or_false(tmp_path):
    with pytest.raises(ConfigError, match="no_verbatim"):
        load_config(write_config(tmp_path, {"no_verbatim": "yes"}))


def test_no_verbatim_with_another_model_is_rejected_before_a_recording_is_made(tmp_path):
    """An unsupported parameter would come back as an HTTP error after the user has
    already spoken. That is the one moment this app must not fail."""
    with pytest.raises(ConfigError, match="scribe_v2"):
        load_config(write_config(tmp_path, {"model_id": "scribe_v1", "no_verbatim": True}))


def test_another_model_is_fine_when_no_verbatim_is_off(tmp_path):
    cfg = load_config(write_config(tmp_path, {"model_id": "scribe_v1", "no_verbatim": False}))
    assert cfg.model_id == "scribe_v1"


def test_filler_words_have_a_georgian_default(tmp_path):
    words = load_config(tmp_path / "absent.json").filler_words
    assert "ააა" in words and "მმმ" in words


def test_filler_words_are_passed_through(tmp_path):
    cfg = load_config(write_config(tmp_path, {"filler_words": ["ააა", "hmm"]}))
    assert cfg.filler_words == ("ააა", "hmm")


def test_filler_removal_is_switched_off_by_an_empty_list(tmp_path):
    assert load_config(write_config(tmp_path, {"filler_words": []})).filler_words == ()


def test_a_one_letter_filler_is_rejected_because_it_would_delete_real_words(tmp_path):
    """A single "ა" would match a Georgian list marker standing on its own."""
    with pytest.raises(ConfigError, match="filler_words"):
        load_config(write_config(tmp_path, {"filler_words": ["ა"]}))


def test_a_filler_with_a_space_in_it_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="filler_words"):
        load_config(write_config(tmp_path, {"filler_words": ["ააა მმმ"]}))


def test_filler_words_must_be_a_list_of_words(tmp_path):
    with pytest.raises(ConfigError, match="filler_words"):
        load_config(write_config(tmp_path, {"filler_words": "ააა"}))
    with pytest.raises(ConfigError, match="filler_words"):
        load_config(write_config(tmp_path, {"filler_words": [1, 2]}))


def test_too_many_filler_words_is_rejected(tmp_path):
    too_many = [f"აა{index}" for index in range(config_module.MAX_FILLER_WORDS + 1)]
    with pytest.raises(ConfigError, match="filler_words"):
        load_config(write_config(tmp_path, {"filler_words": too_many}))


def test_the_rewrite_model_has_a_default(tmp_path):
    assert load_config(tmp_path / "absent.json").rewrite_model == "gemini-2.5-flash"


def test_the_rewrite_prompt_lives_beside_config_json(tmp_path):
    cfg = load_config(tmp_path / "absent.json")
    assert cfg.rewrite_prompt_path.name == "rewrite-prompt.md"
    assert cfg.rewrite_prompt_path.parent == config_module.PROJECT_ROOT


def test_a_second_instruction_file_can_be_named(tmp_path):
    """Keeping several instructions and switching between them is one edit, not a feature
    that had to be built."""
    cfg = load_config(write_config(tmp_path, {"rewrite_prompt_file": "email.md"}))
    assert cfg.rewrite_prompt_path.name == "email.md"


def test_a_prompt_file_outside_the_settings_folder_is_rejected(tmp_path):
    """The window's menu opens this file. A settings value must not be able to point that
    action anywhere on the disk."""
    for bad in ("../secrets.md", "sub/folder.md", r"C:\Windows\note.md"):
        with pytest.raises(ConfigError, match="rewrite_prompt_file"):
            load_config(write_config(tmp_path, {"rewrite_prompt_file": bad}))


def test_a_prompt_file_that_is_not_markdown_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="rewrite_prompt_file"):
        load_config(write_config(tmp_path, {"rewrite_prompt_file": "prompt.exe"}))


def test_the_rewrite_timeout_has_to_be_a_sane_wait(tmp_path):
    with pytest.raises(ConfigError, match="rewrite_timeout_ms"):
        load_config(write_config(tmp_path, {"rewrite_timeout_ms": 120_000}))


def test_a_rewrite_timeout_under_geminis_floor_is_raised_to_it(tmp_path):
    """0.1.5-beta.1 shipped 7000 here and every single rewrite came back a 400 —
    "Manually set deadline 7s is too short" — so the words mode and the rewrite mode
    pasted identical text. Those files still exist, so the value is corrected rather
    than rejected: an app that will not open is a worse answer than one that starts."""
    cfg = load_config(write_config(tmp_path, {"rewrite_timeout_ms": 7_000}))
    assert cfg.rewrite_timeout_ms == 10_000


def test_the_default_rewrite_timeout_clears_that_floor(tmp_path):
    assert load_config(tmp_path / "no-such-file.json").rewrite_timeout_ms >= 10_000


def test_the_gemini_key_is_absent_by_default_and_that_is_not_an_error(tmp_path, monkeypatch):
    """Without it only the rewrite mode is unavailable. Dictation carries on."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert load_config(tmp_path / "absent.json").gemini_api_key == ""


def test_the_gemini_key_is_read_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    assert load_config(tmp_path / "absent.json").gemini_api_key == "test-key-not-real"
