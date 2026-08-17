"""Shared test setup.

The tests contain Georgian text, and the Windows console defaults to cp1252 — so any
`print()` of a transcript, or a run with `pytest -s`, would die on a UnicodeEncodeError
that has nothing to do with the code under test.
"""

import sys


def pytest_configure(config):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
