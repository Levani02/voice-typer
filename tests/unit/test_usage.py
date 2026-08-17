"""Cost accounting. If this is wrong the user is misled about what the tool costs."""

import json

import pytest

from voice_typer.transcriber import UsageLog

PRICE_PER_HOUR = 0.22


@pytest.fixture
def usage(tmp_path):
    return UsageLog(tmp_path / "usage.json", PRICE_PER_HOUR)


def test_starts_at_zero(usage):
    totals = usage.read()
    assert (totals.calls, totals.total_seconds, totals.total_cost_usd) == (0, 0.0, 0.0)


def test_one_hour_costs_the_hourly_rate(usage):
    totals = usage.add(3600)
    assert totals.calls == 1
    assert totals.total_cost_usd == pytest.approx(PRICE_PER_HOUR)


def test_a_short_sentence_costs_well_under_a_cent(usage):
    """The claim made in the README — worth pinning down."""
    totals = usage.add(5)
    assert totals.total_cost_usd < 0.01


def test_totals_accumulate_across_calls(usage):
    usage.add(60)
    usage.add(120)
    totals = usage.add(180)
    assert totals.calls == 3
    assert totals.total_seconds == pytest.approx(360)
    assert totals.total_cost_usd == pytest.approx(PRICE_PER_HOUR * 0.1)


def test_totals_survive_a_restart(tmp_path):
    path = tmp_path / "usage.json"
    UsageLog(path, PRICE_PER_HOUR).add(3600)
    reopened = UsageLog(path, PRICE_PER_HOUR).read()
    assert reopened.total_cost_usd == pytest.approx(PRICE_PER_HOUR)


def test_a_corrupt_file_resets_rather_than_crashing(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert UsageLog(path, PRICE_PER_HOUR).read().calls == 0


def test_the_file_records_the_rate_it_used(tmp_path):
    """So a later price change is visible rather than silently rewriting history."""
    path = tmp_path / "usage.json"
    UsageLog(path, PRICE_PER_HOUR).add(3600)
    assert json.loads(path.read_text(encoding="utf-8"))["price_per_hour_usd"] == PRICE_PER_HOUR


def test_the_directory_is_created_if_it_does_not_exist(tmp_path):
    path = tmp_path / "logs" / "usage.json"
    UsageLog(path, PRICE_PER_HOUR).add(10)
    assert path.exists()
