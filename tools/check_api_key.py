"""Report which ElevenLabs endpoints the current API key can actually reach.

Run it once while the key is unrestricted and once after restricting it. The line that
changes from OK to a rejection names the permission the restriction removed — which is
the one piece of information the ElevenLabs error message itself does not give.

    .venv\\Scripts\\python.exe tools\\check_api_key.py

The key is read from .env and never printed, logged, or included in any output. Only
HTTP status codes and the server's own words about the request are shown.

The speech-to-text probe uploads a third of a second of silence, so at $0.22 per hour it
costs about two thousandths of a cent. It is the only probe that can cost anything.
"""

from __future__ import annotations

import io
import os
import sys
import wave

import httpx
from dotenv import load_dotenv

BASE_URL = "https://api.elevenlabs.io"
TIMEOUT_SECONDS = 30
SILENCE_SECONDS = 0.3
SAMPLE_RATE = 16_000

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Endpoint, and the product area a restricted key has to include for it to answer.
READ_PROBES = (
    ("GET  /v1/user", "user info", "/v1/user"),
    ("GET  /v1/models", "models list", "/v1/models"),
    ("GET  /v1/voices", "text to speech / voices", "/v1/voices"),
)


def build_silence_wav() -> bytes:
    """A short, valid WAV. Enough to be accepted, too short to cost anything."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(b"\x00\x00" * int(SAMPLE_RATE * SILENCE_SECONDS))
    return buffer.getvalue()


def describe(response: httpx.Response) -> str:
    """The server's verdict in one line. This is its words about our request, not the key."""
    if response.status_code < 300:
        return "OK"

    try:
        detail = response.json().get("detail")
    except ValueError:
        return f"HTTP {response.status_code}"

    if isinstance(detail, dict):
        status = detail.get("status", "")
        message = detail.get("message", "")
        return f"HTTP {response.status_code}  {status}  {message}".strip()
    if isinstance(detail, str):
        return f"HTTP {response.status_code}  {detail}"
    return f"HTTP {response.status_code}"


def probe_reads(client: httpx.Client) -> None:
    for label, area, path in READ_PROBES:
        try:
            verdict = describe(client.get(path))
        except httpx.HTTPError as exc:
            verdict = f"could not reach ElevenLabs: {type(exc).__name__}"
        print(f"  {label:<22} {area:<24} {verdict}")


def probe_speech_to_text(client: httpx.Client) -> None:
    files = {"file": ("probe.wav", build_silence_wav(), "audio/wav")}
    data = {"model_id": "scribe_v2", "language_code": "kat"}
    try:
        response = client.post("/v1/speech-to-text", files=files, data=data)
        verdict = describe(response)
    except httpx.HTTPError as exc:
        verdict = f"could not reach ElevenLabs: {type(exc).__name__}"
    print(f"  {'POST /v1/speech-to-text':<22} {'speech to text':<24} {verdict}")


def main() -> int:
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=False)
    key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        print("No ELEVENLABS_API_KEY found in .env — nothing to check.")
        return 1

    print(f"key found: yes ({len(key)} characters)\n")
    with httpx.Client(
        base_url=BASE_URL, headers={"xi-api-key": key}, timeout=TIMEOUT_SECONDS
    ) as client:
        probe_reads(client)
        probe_speech_to_text(client)

    print(
        "\nOK means the key reaches that area. 403 means the key is valid but not scoped "
        "for it.\n401 invalid_api_key means the key itself was not accepted at all — a "
        "different problem\nfrom a missing permission."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
