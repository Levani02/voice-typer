"""Saving the key the user types on a first run.

A packaged executable has no .env.example beside it, so the app writes the file itself.
Two things must hold every time: the rest of the file survives, and the key never leaves
.env — not into a log, not into an exception, not into a second copy on disk.
"""

import pytest

from voice_typer import config as config_module
from voice_typer.config import (
    ConfigError,
    MissingApiKeyError,
    current_gemini_key,
    has_gemini_key,
    load_config,
    save_api_key,
    save_gemini_key,
)

SECRET = "sk_not_a_real_key_9876543210"
GEMINI_SECRET = "AIza_not_a_real_key_0123456789"


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch, tmp_path):
    """Never touch the developer's own .env."""
    monkeypatch.setattr(config_module, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LOG_TRANSCRIPTS", raising=False)
    return tmp_path / ".env"


def test_a_missing_key_is_its_own_kind_of_failure(tmp_path):
    """It is the only startup problem the app answers with a question, not a message."""
    with pytest.raises(MissingApiKeyError):
        load_config(tmp_path / "no-such-file.json")


def test_the_key_is_written_and_takes_effect_immediately(isolate_env, tmp_path):
    save_api_key(SECRET)

    assert isolate_env.read_text(encoding="utf-8").count(SECRET) == 1
    # No restart: the running app reads the key from the environment.
    assert load_config(tmp_path / "no-such-file.json").api_key == SECRET


def test_the_rest_of_an_existing_file_is_left_alone(isolate_env):
    isolate_env.write_text(
        "# a comment the user wrote\nLOG_TRANSCRIPTS=true\nELEVENLABS_API_KEY=old-key\n",
        encoding="utf-8",
    )

    save_api_key(SECRET)

    written = isolate_env.read_text(encoding="utf-8")
    assert "# a comment the user wrote" in written
    assert "LOG_TRANSCRIPTS=true" in written
    assert "old-key" not in written
    assert SECRET in written


def test_a_key_is_added_when_the_file_has_no_line_for_it(isolate_env):
    isolate_env.write_text("LOG_TRANSCRIPTS=false\n", encoding="utf-8")

    save_api_key(SECRET)

    assert SECRET in isolate_env.read_text(encoding="utf-8")
    assert "LOG_TRANSCRIPTS=false" in isolate_env.read_text(encoding="utf-8")


def test_surrounding_whitespace_is_not_saved_with_the_key(isolate_env):
    """Pasting from a web page picks up a newline more often than not."""
    save_api_key(f"  {SECRET}\n")

    assert f"ELEVENLABS_API_KEY={SECRET}" in isolate_env.read_text(encoding="utf-8")


def test_an_empty_key_is_refused_rather_than_written(isolate_env):
    with pytest.raises(ConfigError):
        save_api_key("   ")
    assert not isolate_env.exists()


def test_the_key_never_appears_in_the_refusal(isolate_env):
    try:
        save_api_key("")
    except ConfigError as exc:
        assert "sk_" not in str(exc)


def test_the_gemini_key_is_saved_the_same_way_and_takes_effect_at_once(isolate_env):
    """The second key exists so the rewrite mode can be switched on from inside the app
    rather than by opening a settings file in Notepad."""
    save_gemini_key(GEMINI_SECRET)

    assert f"GEMINI_API_KEY={GEMINI_SECRET}" in isolate_env.read_text(encoding="utf-8")
    assert current_gemini_key() == GEMINI_SECRET
    assert has_gemini_key()


def test_saving_one_key_never_disturbs_the_other(isolate_env):
    """Both live in the same file, and writing either one rewrites it. This is the test
    that stops the app deleting a key the user cannot get back without a trip to a
    website."""
    save_api_key(SECRET)
    save_gemini_key(GEMINI_SECRET)

    written = isolate_env.read_text(encoding="utf-8")
    assert f"ELEVENLABS_API_KEY={SECRET}" in written
    assert f"GEMINI_API_KEY={GEMINI_SECRET}" in written

    save_api_key("sk_a_replacement_key_1111")

    written = isolate_env.read_text(encoding="utf-8")
    assert f"GEMINI_API_KEY={GEMINI_SECRET}" in written  # untouched
    assert SECRET not in written


def test_no_gemini_key_is_not_an_error_it_is_a_mode_that_says_so(isolate_env):
    """The whole reason the second field may be left empty."""
    assert not has_gemini_key()
    assert current_gemini_key() == ""


def test_an_empty_gemini_key_is_refused_rather_than_written(isolate_env):
    with pytest.raises(ConfigError):
        save_gemini_key("   ")
    assert not isolate_env.exists()


def test_settings_are_written_out_beside_a_packaged_build(monkeypatch, tmp_path):
    """Inside an executable the bundled copy lives in a folder that is deleted on exit."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "config.json").write_text('{"hotkey": "f8"}', encoding="utf-8")
    beside_the_exe = tmp_path / "config.json"

    monkeypatch.setattr(config_module.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(config_module, "CONFIG_PATH", beside_the_exe)

    config_module.ensure_settings_file()

    assert beside_the_exe.read_text(encoding="utf-8") == '{"hotkey": "f8"}'


def test_existing_settings_are_never_overwritten(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "config.json").write_text('{"hotkey": "f8"}', encoding="utf-8")
    beside_the_exe = tmp_path / "config.json"
    beside_the_exe.write_text('{"hotkey": "f4"}', encoding="utf-8")

    monkeypatch.setattr(config_module.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(config_module, "CONFIG_PATH", beside_the_exe)

    config_module.ensure_settings_file()

    assert beside_the_exe.read_text(encoding="utf-8") == '{"hotkey": "f4"}'


def test_running_from_source_writes_no_settings_file(monkeypatch, tmp_path):
    beside_the_exe = tmp_path / "config.json"
    monkeypatch.delattr(config_module.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(config_module, "CONFIG_PATH", beside_the_exe)

    config_module.ensure_settings_file()

    assert not beside_the_exe.exists()
