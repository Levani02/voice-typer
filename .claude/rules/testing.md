# Testing Rules

Testing is automatic. The user never runs tests and never sees test output — only the
plain-language verdict and, where it applies, a screenshot.

## What can and cannot be automated here

This is a desktop app that touches a microphone, a global keyboard hook, and the Windows
clipboard. Be honest about the split:

| Area | How it is verified |
| --- | --- |
| Hold-vs-tap timing logic | unit test with injected timestamps — no real keyboard |
| Config loading and validation | unit test, including malformed and out-of-range values |
| WAV assembly from PCM chunks | unit test — byte count, header, sample rate |
| Usage and cost accounting | unit test with fixed durations |
| ElevenLabs call | integration test with the SDK **mocked** — never a real call |
| State machine transitions | unit test driving the machine with fake events |
| Microphone capture | by hand — record, play back, listen |
| Global hotkey | by hand — press the key, watch the console |
| Paste into another app | by hand — with a screenshot |

Anything in the bottom three rows is verified by the user watching the screen. Claude does
not claim it works without that.

## Triggers — run without being asked

| Change | Action |
| --- | --- |
| any new function or module | at least one test: happy path plus one error case |
| bug fix | **regression test first**, then the fix, then confirm the test passes |
| dependency added or changed | run the whole suite, to catch breakage |
| before any commit | full suite — a failing suite blocks the commit |
| anything touching timing thresholds | the timing tests, plus a manual press-and-hold check |

## Money

Tests never spend money. The ElevenLabs client is mocked in every automated test. A real
API call happens only during a manual end-to-end check that the user is present for.

Store one short Georgian sample WAV under `tests/fixtures/` for the mocked tests to feed in.

## Non-negotiable

- Never commit with failing tests
- Never `--no-verify`
- Never delete, skip, or disable a test to make the suite green
- Fix the code, not the test — unless the test is genuinely wrong, and then say why

## Reporting

✗ "pytest: 14 passed, coverage 82%" · "AssertionError on line 40" · "run `pytest`"

✓ "შევამოწმე — მუშაობს" · "ტაიმინგის ლოგიკა გავტესტე, სწორად ცნობს დაჭერასა და გეჭირვას" ·
"რაღაც გატყდა: [plain explanation]. ვასწორებ."

After a manual check, ask: **ნახე შედეგი — კარგად გამოიყურება?**
