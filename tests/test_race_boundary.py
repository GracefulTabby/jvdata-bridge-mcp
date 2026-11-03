"""境界の選択を、同日・他場・欠測・部分速報の合成データで検証する。"""

import asyncio
import sqlite3
from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest

from jvdata_bridge_mcp.tools import _race_boundary as boundary
from jvdata_bridge_mcp.tools._observation_scope import JST
from jvdata_bridge_mcp.tools.race import forecast_context


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    now = datetime(2024, 1, 6, 14, 30, tzinfo=JST)
    monkeypatch.setattr(boundary, "now_jst", lambda: now)
    monkeypatch.setattr(forecast_context, "now_jst", lambda: now)
    return now


def test_unknown_target_never_falls_back_to_unbounded_history(
    monkeypatch: pytest.MonkeyPatch, clock: datetime
) -> None:
    monkeypatch.setattr(boundary, "fetch_rows", AsyncMock(return_value=[]))
    with pytest.raises(ValueError, match="unavailable"):
        asyncio.run(boundary.resolve_race_boundary(object(), "2024010605010111"))


@pytest.mark.parametrize("start", ["2024-01-06T15:40:00", "2024-01-06T06:40:00+00:00"])
def test_target_start_is_interpreted_in_jst(monkeypatch: pytest.MonkeyPatch, clock: datetime, start: str) -> None:
    monkeypatch.setattr(
        boundary,
        "fetch_rows",
        AsyncMock(
            return_value=[
                {
                    "race_date": "2024-01-06",
                    "scheduled_start_at": start,
                }
            ]
        ),
    )
    result = asyncio.run(boundary.resolve_race_boundary(object(), "2024010605010111"))
    assert result.history_before_at == clock.isoformat()
    assert result.scheduled_start_at == "2024-01-06T15:40:00+09:00"
    assert result.parameters()["boundary_date"] == "2024-01-06"


def test_future_race_without_time_uses_current_known_information(
    monkeypatch: pytest.MonkeyPatch, clock: datetime
) -> None:
    monkeypatch.setattr(
        boundary,
        "fetch_rows",
        AsyncMock(
            return_value=[
                {
                    "race_date": "2024-01-07",
                    "scheduled_start_at": None,
                }
            ]
        ),
    )
    result = asyncio.run(boundary.resolve_race_boundary(object(), "2024010705010211"))
    assert result.history_before_at == clock.isoformat() and result.scheduled_start_at is None


def test_completed_results_include_earlier_other_venue_but_exclude_future_partial_and_target() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH DATABASE ':memory:' AS jv_data")
    db.execute("CREATE TABLE jv_data.races (race_id TEXT, scheduled_start_at TEXT)")
    db.execute("CREATE TABLE runs (race_id TEXT,race_date TEXT,is_complete_result INTEGER)")
    candidates = [
        ("previous-day", "2024-01-05", None, 1),
        ("other-venue-high-code", "2024-01-06", "2024-01-06 13:00:00", 1),
        ("target", "2024-01-06", "2024-01-06 13:15:00", 1),
        ("partial", "2024-01-06", "2024-01-06 13:30:00", 0),
        ("equal-time", "2024-01-06", "2024-01-06 14:30:00", 1),
        ("later", "2024-01-06", "2024-01-06 15:00:00", 1),
        ("unknown-time", "2024-01-06", None, 1),
        ("future-day", "2024-01-07", "2024-01-07 09:00:00", 1),
    ]
    for code, day, start, complete in candidates:
        db.execute("INSERT INTO jv_data.races VALUES (?,?)", (code, start))
        db.execute("INSERT INTO runs VALUES (?,?,?)", (code, day, complete))
    # Only adapt PostgreSQL date/timestamp casts to SQLite's ISO text comparison.
    predicate = boundary.before_boundary_sql("r").replace("CAST(:boundary_date AS date)", ":boundary_date")
    predicate = predicate.replace("CAST(:boundary_local AS timestamp)", ":boundary_local")
    found = db.execute(
        "SELECT race_id FROM runs r WHERE is_complete_result=1 AND " + predicate,
        {"race_code": "target", "boundary_date": "2024-01-06", "boundary_local": "2024-01-06 14:30:00"},
    ).fetchall()
    assert {row[0] for row in found} == {"previous-day", "other-venue-high-code"}
    db.close()


def test_year_period_is_relative_to_target_without_advancing_the_current_boundary() -> None:
    assert boundary.period_dates(date(2025, 1, 1), "current_year") == ("2025-01-01", "2026-01-01")
    assert boundary.period_dates(date(2025, 1, 1), "previous_year") == ("2024-01-01", "2025-01-01")


def test_current_day_observation_needs_explicit_time_and_rejects_future(clock: datetime) -> None:
    obs = forecast_context.Observation(
        race_code="2024010605010111",
        horse_number=1,
        kind="paddock",
        observed_date=date(2024, 1, 6),
        source="human",
        description="synthetic observation",
    )
    with pytest.raises(ValueError, match="observed_at"):
        forecast_context.eligible_observations([obs], obs.race_code, {1}, date(2024, 1, 6))
    obs.observed_at = datetime(2024, 1, 6, 14, tzinfo=JST)
    assert forecast_context.eligible_observations([obs], obs.race_code, {1}, date(2024, 1, 6)) == [obs]
    obs.observed_at = datetime(2024, 1, 6, 15, tzinfo=JST)
    with pytest.raises(ValueError, match="before now"):
        forecast_context.eligible_observations([obs], obs.race_code, {1}, date(2024, 1, 6))


@pytest.mark.parametrize("start,valid", [("2024-01-06T13:00:00", True), ("2024-01-06T15:00:00", False), (None, False)])
def test_same_day_evidence_race_order_is_verified_from_metadata(
    monkeypatch: pytest.MonkeyPatch, clock: datetime, start: str | None, valid: bool
) -> None:
    from jvdata_bridge_mcp.tools.race.entries import EntriesListOutput

    code = "2024010605010111"
    evidence = "2024010605010101"
    obs = forecast_context.Observation(
        race_code=code,
        evidence_race_code=evidence,
        horse_number=1,
        kind="trouble",
        observed_date=date(2024, 1, 6),
        observed_at=datetime(2024, 1, 6, 14, tzinfo=JST),
        source="human",
        description="synthetic earlier-race observation",
    )
    entry = EntriesListOutput(
        entries=[
            {
                "race_code": code,
                "horse_number": 1,
                "horse_id": "synthetic",
                "horse_name": "Horse",
            }
        ],
        race_date="2024-01-06",
    )

    async def read(db: object, sql: str, parameters: object, **kwargs: object) -> list[dict[str, object]]:
        if sql == forecast_context._RACE_SQL:
            return [{"race_code": code, "race_date": "2024-01-06", "scheduled_start_at": "2024-01-06T15:40:00"}]
        assert "scheduled_start_at" in sql and "ANY(:codes)" in sql
        return [{"race_id": evidence, "scheduled_start_at": start}]

    monkeypatch.setattr(forecast_context, "fetch_rows", read)
    monkeypatch.setattr(forecast_context, "build_entries_list_tool", lambda _db: AsyncMock(return_value=entry))
    call = forecast_context.build_race_forecast_context_tool(object())(code, sections=[], observations=[obs])
    if valid:
        assert asyncio.run(call).observations == [obs]
    else:
        with pytest.raises(ValueError, match="known scheduled start|precede the observation"):
            asyncio.run(call)
