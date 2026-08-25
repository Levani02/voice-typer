"""Settings: the secret comes from .env, everything else from config.json.

Both are validated at import time of `load_config`, so a bad value fails immediately
with a sentence the user can act on, rather than half-way through a recording.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _project_root() -> Path:
    """Where the settings, the key and the logs live.

    Three different answers, for three different situations:

    * **From source** — the repository, so everything sits beside the code being edited.
    * **A packaged Windows build** — next to the .exe, which keeps it portable: copy the
      folder to another machine and the settings go with it.
    * **A packaged macOS build** — under Application Support. A .app bundle is meant to be
      read-only, is replaced wholesale on every update, and may be launched from a
      read-only disk image, so writing inside it would lose the key sooner or later.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent.parent
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "voice-typer"
    return Path(sys.executable).resolve().parent


PROJECT_ROOT = _project_root()
CONFIG_PATH = PROJECT_ROOT / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"
LOGS_DIR = PROJECT_ROOT / "logs"

# The one setting the user has to supply. Named once so that no line in this repository
# ever holds the name and a value together — that shape is what a leaked key looks like.
API_KEY_SETTING = "ELEVENLABS_API_KEY"

# The second key, and the only other one. Optional: without it the rewrite mode reports
# itself unavailable and the words mode carries on exactly as before.
GEMINI_KEY_SETTING = "GEMINI_API_KEY"

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
    # Ask ElevenLabs not to write down the hesitation sounds and false starts it hears.
    # Only scribe_v2 honours it, which is why the two settings are validated together.
    "no_verbatim": True,
    "price_per_hour_usd": 0.22,
    "keyterms": [],
    # A second net, on this side of the wire, for the hesitations the model still
    # writes down. Whole tokens only, and an empty list switches it off.
    #
    # Georgian only: a Latin entry would reach for capitalised words that are
    # somebody's name — "Um" is a surname — and this app transcribes Georgian.
    "filler_words": ["ააა", "ეეე", "ოოო", "მმმ", "ჰმმ", "ემმ", "უუუ"],
    # The rewrite mode, which is the one place in this app where a model is allowed to
    # touch what the user said. The instruction itself is a markdown file beside this one,
    # so it can be edited and re-read without a restart — see `rewrite-prompt.md`.
    "rewrite_model": "gemini-2.5-flash",
    "rewrite_prompt_file": "rewrite-prompt.md",
    # Gemini refuses a deadline under ten seconds outright — "Manually set deadline 7s is
    # too short" comes back as a 400 before the model is ever asked, so this is not a
    # dial that can be tuned down to the second or two a rewrite actually takes. Twelve
    # sits clear of that floor; the wait only reaches it when something is already wrong,
    # and then the raw transcript is pasted instead.
    "rewrite_timeout_ms": 12000,
    # Below this, the model is not asked. A short utterance has too little context to tell
    # a fragment from a command, and that is the shape that invents whole paragraphs.
    "rewrite_min_chars": 40,
    "prune_takes_after_days": 7,
    "window_scale": 1.0,
    "content_scale": 1.0,
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
    # The lower bound is Gemini's, not ours: it rejects any deadline below ten seconds
    # with a 400, so a smaller value here would fail every single rewrite rather than
    # merely hurry it. Over half a minute nobody is still waiting for their own sentence.
    "rewrite_timeout_ms": (10_000, 30_000),
    "rewrite_min_chars": (0, 1_000),
    # How large the recorder window is drawn, on top of the display's own scaling. Below
    # about a third the Georgian labels stop being legible at any DPI.
    "window_scale": (0.3, 2.0),
    # Text and icons, on top of `window_scale`. Above about 1.5 the labels outgrow the
    # buttons they sit in; the card does not grow to meet them.
    "content_scale": (0.6, 1.5),
}

# Above this many key terms ElevenLabs bills a 20-second minimum per request, which would
# cost several times more than a short dictation. Rejected at startup rather than silently
# trimmed, so the user is not billed for a setting they thought was in effect.
MAX_KEYTERMS = 100

# Far past any language's stock of hesitation noises. A longer list is a mistake, and
# every entry widens a pattern that runs on every single transcript.
MAX_FILLER_WORDS = 50

# `no_verbatim` is documented as a scribe_v2 feature. Sending it with another model is not
# quietly ignored — it is a request the server may reject, in the middle of a dictation.
NO_VERBATIM_MODELS = ("scribe_v2",)


class ConfigError(Exception):
    """A setting is missing or unusable. The message is shown to the user verbatim."""


class MissingApiKeyError(ConfigError):
    """No key yet — the one failure the app can fix by asking, instead of giving up.

    Kept apart from every other ConfigError because the response is different: a bad
    hotkey name is a mistake to report, while a missing key on a first run is simply the
    question that has not been asked yet.
    """


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
    no_verbatim: bool
    price_per_hour_usd: float
    keyterms: tuple[str, ...]
    filler_words: tuple[str, ...]
    rewrite_model: str
    rewrite_prompt_path: Path
    rewrite_timeout_ms: int
    rewrite_min_chars: int
    gemini_api_key: str
    prune_takes_after_days: int
    window_scale: float
    content_scale: float
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

    for name in ("restore_clipboard", "no_verbatim"):
        if not isinstance(values[name], bool):
            raise ConfigError(f"config.json: '{name}' must be true or false")

    for name in ("hotkey", "language_code", "model_id", "rewrite_model", "rewrite_prompt_file"):
        # Stripped before the check, because these are stripped before they are used. A
        # `rewrite_instruction` of three spaces would otherwise pass here, arrive empty,
        # and turn rewrite mode into a switch that reports itself on and does nothing.
        if not isinstance(values[name], str) or not values[name].strip():
            raise ConfigError(f"config.json: '{name}' must be a non-empty text value")

    device = values["input_device"]
    if device is not None and not isinstance(device, int | str):
        raise ConfigError("config.json: 'input_device' must be null, a number, or a device name")

    _validate_prompt_file(str(values["rewrite_prompt_file"]))
    _validate_keyterms(values["keyterms"])
    _validate_filler_words(values["filler_words"])
    _validate_no_verbatim(values)
    _validate_hotkey(str(values["hotkey"]))


def _validate_hotkey(name: str) -> None:
    """Reject an unknown key name here rather than when the listener starts.

    `main.py` used to catch this from `app.start()`, but the listener is built in
    `App.__init__` — thirteen lines earlier — so the friendly message was unreachable and
    the user got a bare crash dialog instead.
    """
    from voice_typer.hotkey import parse_key  # local: keeps pynput off the import path

    try:
        parse_key(name)
    except ValueError as exc:
        raise ConfigError(f"config.json: {exc}") from exc


def _validate_prompt_file(name: str) -> None:
    """A plain filename beside config.json — no folders, no climbing out of it.

    The window's menu opens this file, and this project's rule is that a menu action opens
    a fixed path rather than one a settings file chose. Confining the value to a bare
    `.md` name in the settings folder is what keeps that true while still letting the user
    keep several instructions and switch between them by name.
    """
    if "/" in name or "\\" in name or ".." in name:
        raise ConfigError(
            "config.json: 'rewrite_prompt_file' must be a file name beside config.json, "
            'not a path — for example "rewrite-prompt.md"'
        )
    if not name.lower().endswith(".md"):
        raise ConfigError("config.json: 'rewrite_prompt_file' must end with .md")


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


def _validate_filler_words(filler_words: object) -> None:
    """The hesitation sounds to drop. Every entry is a shape, not a spelling.

    Two letters minimum, and no spaces. A one-letter entry would match a Georgian list
    marker or an initial standing on its own, and delete it silently — the exact class of
    failure this app must never have.
    """
    if not isinstance(filler_words, list) or any(
        not isinstance(word, str) for word in filler_words
    ):
        raise ConfigError(
            'config.json: \'filler_words\' must be a list of words, for example ["ააა", "მმმ"]'
        )
    for word in filler_words:
        stripped = word.strip()
        if len(stripped) < 2 or " " in stripped:
            raise ConfigError(
                f"config.json: 'filler_words' entry {word!r} is unusable — write at least two "
                f'letters and no spaces, for example "ააა"'
            )
    if len(filler_words) > MAX_FILLER_WORDS:
        raise ConfigError(
            f"config.json: 'filler_words' has {len(filler_words)} entries. Keep it to "
            f"{MAX_FILLER_WORDS} — every entry is checked against every transcript."
        )


def _validate_no_verbatim(values: dict[str, object]) -> None:
    """Filler removal is a scribe_v2 feature; asking for it elsewhere is a broken request.

    Caught at startup rather than mid-dictation: an HTTP error arriving after someone has
    already spoken is the one moment this app must not fail.
    """
    if not values["no_verbatim"]:
        return
    model = str(values["model_id"])
    if model not in NO_VERBATIM_MODELS:
        raise ConfigError(
            f"config.json: 'no_verbatim' only works with {NO_VERBATIM_MODELS[0]}, but "
            f'\'model_id\' is {model!r}. Either set "model_id": "{NO_VERBATIM_MODELS[0]}" '
            f'or set "no_verbatim": false.'
        )


def _require_api_key() -> str:
    """Return the ElevenLabs key, or explain what to do about its absence.

    The value itself is never logged, printed, or included in an exception message.
    """
    key = (os.environ.get(API_KEY_SETTING) or "").strip()
    if key:
        return key

    where = f".env exists but {API_KEY_SETTING} is empty" if ENV_PATH.exists() else ".env not found"
    raise MissingApiKeyError(
        f"No ElevenLabs API key ({where}). Get one at https://elevenlabs.io/app/settings/api-keys"
    )


# What a fresh .env starts as, when the app has to create one itself. Deliberately not
# read from .env.example: a packaged executable ships without the repository around it.
ENV_HEADER = (
    "# voice-typer — private settings. Never share this file and never commit it.",
    "",
)


def _env_lines_with_key(existing: list[str], key: str) -> list[str]:
    """The file's lines with the key line replaced, or added if it was not there."""
    prefix = f"{API_KEY_SETTING}="
    updated = [prefix + key if line.strip().startswith(prefix) else line for line in existing]
    if not any(line.startswith(prefix) for line in updated):
        updated.append(prefix + key)
    return updated


def _bundled(name: str) -> Path | None:
    """A file carried inside a packaged executable, if there is one."""
    root = getattr(sys, "_MEIPASS", None)
    if not root:
        return None
    candidate = Path(root) / name
    return candidate if candidate.is_file() else None


def ensure_settings_file() -> None:
    """Put the editable files where the user can find them, on a packaged first run.

    Running from source they are already there. A packaged build carries its copies in a
    folder that is deleted when the app exits, so they are written out beside the
    executable once — otherwise the menu's "Settings" and "rewrite instruction" entries
    would open files that vanish.
    """
    for target in (CONFIG_PATH, PROJECT_ROOT / DEFAULTS["rewrite_prompt_file"]):  # type: ignore[operator]
        if target.exists():
            continue
        bundled = _bundled(target.name)
        if bundled is None:
            continue
        with contextlib.suppress(OSError):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundled.read_bytes())


def save_api_key(key: str) -> None:
    """Put the user's key into .env and make it live in this process straight away.

    Called from the first-run window. Every other part of the app reads the key through
    the environment, so setting it here means the app carries on without a restart.

    The value is never logged and never included in an error message.
    """
    key = key.strip()
    if not key:
        raise ConfigError("The key is empty.")

    if ENV_PATH.exists():
        existing = ENV_PATH.read_text(encoding="utf-8").splitlines()
    else:
        existing = list(ENV_HEADER)

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(_env_lines_with_key(existing, key)) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        ENV_PATH.chmod(0o600)  # no effect on Windows, correct everywhere else
    os.environ[API_KEY_SETTING] = key


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
        no_verbatim=bool(values["no_verbatim"]),
        price_per_hour_usd=float(values["price_per_hour_usd"]),
        keyterms=tuple(values["keyterms"]),  # type: ignore[arg-type]
        filler_words=tuple(values["filler_words"]),  # type: ignore[arg-type]
        rewrite_model=str(values["rewrite_model"]).strip(),
        rewrite_prompt_path=PROJECT_ROOT / str(values["rewrite_prompt_file"]).strip(),
        rewrite_timeout_ms=int(values["rewrite_timeout_ms"]),
        rewrite_min_chars=int(values["rewrite_min_chars"]),
        # Absent is a normal state, not an error: it only switches one mode off.
        gemini_api_key=(os.environ.get(GEMINI_KEY_SETTING) or "").strip(),
        prune_takes_after_days=int(values["prune_takes_after_days"]),
        window_scale=float(values["window_scale"]),
        content_scale=float(values["content_scale"]),
        log_transcripts=os.environ.get("LOG_TRANSCRIPTS", "").strip().lower() == "true",
    )
