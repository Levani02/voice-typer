"""Settings: the secret comes from .env, everything else from config.json.

Both are validated at import time of `load_config`, so a bad value fails immediately
with a sentence the user can act on, rather than half-way through a recording.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"
LOGS_DIR = PROJECT_ROOT / "logs"

# Every setting the user may change, with the value used when config.json omits it.
DEFAULTS: dict[str, object] = {
    "hotkey": "f9",
    "hold_threshold_ms": 400,
    "max_recording_seconds": 300,
    "min_recording_ms": 300,
    "input_device": None,
    "sample_rate": 16000,
    "restore_clipboard": True,
    "clipboard_restore_delay_ms": 300,
    "language_code": "kat",
    "model_id": "scribe_v2",
    "price_per_hour_usd": 0.22,
    "keyterms": [],
    "prune_takes_after_days": 7,
}

# name -> (minimum, maximum), inclusive. Guards against a typo turning into a bill.
NUMERIC_RANGES: dict[str, tuple[float, float]] = {
    "hold_threshold_ms": (50, 5_000),
    "max_recording_seconds": (5, 3_600),
    "min_recording_ms": (0, 5_000),
    "sample_rate": (8_000, 48_000),
    "clipboard_restore_delay_ms": (0, 5_000),
    "price_per_hour_usd": (0, 100),
    "prune_takes_after_days": (1, 365),
}

# Above this many key terms ElevenLabs bills a 20-second minimum per request, which would
# cost several times more than a short dictation. Rejected at startup rather than silently
# trimmed, so the user is not billed for a setting they thought was in effect.
MAX_KEYTERMS = 100


class ConfigError(Exception):
    """A setting is missing or unusable. The message is shown to the user verbatim."""


@dataclass(frozen=True)
class Config:
    """Read-only settings. Built once at startup and never mutated afterwards."""

    api_key: str
    hotkey: str
    hold_threshold_ms: int
    max_recording_seconds: int
    min_recording_ms: int
    input_device: int | str | None
    sample_rate: int
    restore_clipboard: bool
    clipboard_restore_delay_ms: int
    language_code: str
    model_id: str
    price_per_hour_usd: float
    keyterms: tuple[str, ...]
    prune_takes_after_days: int
    log_transcripts: bool


def _read_config_file(path: Path) -> dict[str, object]:
    """Return config.json merged over the defaults, or the defaults if it is absent."""
    if not path.exists():
        return dict(DEFAULTS)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json is not valid JSON (line {exc.lineno}): {exc.msg}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config.json must contain a JSON object, not a list or value")

    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ConfigError(
            f"config.json has unknown setting(s): {', '.join(sorted(unknown))}. "
            f"Valid names: {', '.join(sorted(DEFAULTS))}"
        )

    return {**DEFAULTS, **raw}


def _validate_ranges(values: dict[str, object]) -> None:
    """Reject numbers outside their sane range before they reach the audio or API layer."""
    for name, (low, high) in NUMERIC_RANGES.items():
        value = values[name]
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ConfigError(f"config.json: '{name}' must be a number, got {value!r}")
        if not low <= value <= high:
            raise ConfigError(
                f"config.json: '{name}' must be between {low} and {high}, got {value}"
            )

    if not isinstance(values["restore_clipboard"], bool):
        raise ConfigError("config.json: 'restore_clipboard' must be true or false")

    for name in ("hotkey", "language_code", "model_id"):
        if not isinstance(values[name], str) or not values[name]:
            raise ConfigError(f"config.json: '{name}' must be a non-empty text value")

    device = values["input_device"]
    if device is not None and not isinstance(device, int | str):
        raise ConfigError("config.json: 'input_device' must be null, a number, or a device name")

    _validate_keyterms(values["keyterms"])


def _validate_keyterms(keyterms: object) -> None:
    """Words the transcriber should expect — names, jargon, anything it would misspell."""
    if not isinstance(keyterms, list) or any(not isinstance(term, str) for term in keyterms):
        raise ConfigError(
            "config.json: 'keyterms' must be a list of words, for example [\"სოხუმი\"]"
        )
    if len(keyterms) > MAX_KEYTERMS:
        raise ConfigError(
            f"config.json: 'keyterms' has {len(keyterms)} entries. Keep it to {MAX_KEYTERMS} — "
            f"above that ElevenLabs charges a 20-second minimum for every recording, which "
            f"would cost several times more per sentence."
        )


def _require_api_key() -> str:
    """Return the ElevenLabs key, or explain what to do about its absence.

    The value itself is never logged, printed, or included in an exception message.
    """
    key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if key:
        return key

    where = ".env exists but ELEVENLABS_API_KEY is empty" if ENV_PATH.exists() else ".env not found"
    raise ConfigError(
        f"No ElevenLabs API key ({where}). Copy .env.example to .env and paste your key "
        f"into it — get one at https://elevenlabs.io/app/settings/api-keys"
    )


def load_config(config_path: Path | None = None) -> Config:
    """Build the settings object. Raises ConfigError with a user-readable message."""
    load_dotenv(ENV_PATH, override=False)
    values = _read_config_file(config_path or CONFIG_PATH)
    _validate_ranges(values)

    return Config(
        api_key=_require_api_key(),
        hotkey=str(values["hotkey"]).strip().lower(),
        hold_threshold_ms=int(values["hold_threshold_ms"]),
        max_recording_seconds=int(values["max_recording_seconds"]),
        min_recording_ms=int(values["min_recording_ms"]),
        input_device=values["input_device"],  # type: ignore[arg-type]
        sample_rate=int(values["sample_rate"]),
        restore_clipboard=bool(values["restore_clipboard"]),
        clipboard_restore_delay_ms=int(values["clipboard_restore_delay_ms"]),
        language_code=str(values["language_code"]),
        model_id=str(values["model_id"]),
        price_per_hour_usd=float(values["price_per_hour_usd"]),
        keyterms=tuple(values["keyterms"]),  # type: ignore[arg-type]
        prune_takes_after_days=int(values["prune_takes_after_days"]),
        log_transcripts=os.environ.get("LOG_TRANSCRIPTS", "").strip().lower() == "true",
    )
