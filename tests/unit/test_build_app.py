"""The one part of the macOS build that can be checked without a Mac.

A bundle that does not declare the microphone still builds, still launches, and still
records — it just records silence, and the user only finds out when the transcript comes
back empty. `declared_usage_descriptions` is what stops that reaching a release, so it is
worth testing that it reads a real plist rather than assuming.
"""

import plistlib

import pytest

from tools.build_app import MACOS_USAGE_DESCRIPTIONS, declared_usage_descriptions


def make_bundle(root, plist_contents):
    """A directory shaped like a .app, with whatever Info.plist the test needs."""
    contents = root / "voice-typer.app" / "Contents"
    contents.mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as handle:
        plistlib.dump(plist_contents, handle)
    return root / "voice-typer.app"


def test_a_bundle_carrying_the_usage_strings_is_recognised(tmp_path):
    bundle = make_bundle(tmp_path, {"CFBundleName": "voice-typer", **MACOS_USAGE_DESCRIPTIONS})

    assert declared_usage_descriptions(bundle) == set(MACOS_USAGE_DESCRIPTIONS)


def test_pyinstallers_own_plist_is_reported_as_missing_the_microphone(tmp_path):
    """This is the v0.1.0 bundle: everything PyInstaller writes, and nothing more."""
    bundle = make_bundle(
        tmp_path,
        {
            "CFBundleName": "voice-typer",
            "CFBundleIdentifier": "com.example.voice-typer",
            "NSHighResolutionCapable": True,
        },
    )

    assert "NSMicrophoneUsageDescription" not in declared_usage_descriptions(bundle)


def test_an_empty_usage_string_counts_as_absent(tmp_path):
    """macOS ignores a blank description, so the build must not accept one either."""
    bundle = make_bundle(tmp_path, {"NSMicrophoneUsageDescription": ""})

    assert declared_usage_descriptions(bundle) == set()


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda path: None, id="no Info.plist at all"),
        pytest.param(lambda path: path.write_bytes(b"not a plist"), id="unreadable Info.plist"),
    ],
)
def test_a_bundle_without_a_readable_plist_declares_nothing(tmp_path, damage):
    contents = tmp_path / "voice-typer.app" / "Contents"
    contents.mkdir(parents=True)
    damage(contents / "Info.plist")

    assert declared_usage_descriptions(tmp_path / "voice-typer.app") == set()
