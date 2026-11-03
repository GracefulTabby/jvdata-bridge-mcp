"""対象レースから自動で決まる境界と、現在観測の統合を検証する。"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from jvdata_bridge_mcp.tools import _race_boundary as boundary_module
from jvdata_bridge_mcp.tools.prediction import features
from jvdata_bridge_mcp.tools.race import live_context
from jvdata_bridge_mcp.tools.race.forecast_context import RaceForecastContextOutput

JST = ZoneInfo("Asia/Tokyo")
TODAY = "2024010605010111"
TOMORROW = "2024010705010211"


def test_fixed_model_features_keep_their_training_aggregation_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    read = AsyncMock(return_value=[])
    coverage = AsyncMock()
    monkeypatch.setattr(features, "fetch_rows", read)
    monkeypatch.setattr(features, "ensure_speed_quality_available", coverage)
    result = asyncio.run(features.build_get_horse_feature_matrix_tool(object())(TODAY))
    assert result.features == []
    coverage.assert_awaited_once()
    sql, params = read.await_args.args[1:3]
    assert "scheduled_start_at" not in sql
    assert params == {"race_code": TODAY, "limit": 100}


def test_public_context_rejects_past_date_without_database_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(boundary_module, "now_jst", lambda: datetime(2024, 1, 6, 12, tzinfo=JST))
    read = AsyncMock()
    monkeypatch.setattr(boundary_module, "fetch_rows", read)
    with pytest.raises(ValueError, match="historical replay"):
        asyncio.run(live_context.build_race_forecast_context_tool(object())("2024010505010111"))
    read.assert_not_awaited()


@pytest.mark.parametrize("start", [None, "2024-01-06T12:00:00", "2024-01-06T11:00:00"])
def test_context_rejects_missing_or_elapsed_start(monkeypatch: pytest.MonkeyPatch, start: str | None) -> None:
    monkeypatch.setattr(boundary_module, "now_jst", lambda: datetime(2024, 1, 6, 12, tzinfo=JST))
    read = AsyncMock(return_value=[{"race_date": "2024-01-06", "scheduled_start_at": start}])
    monkeypatch.setattr(boundary_module, "fetch_rows", read)
    with pytest.raises(ValueError, match="scheduled start|upcoming race"):
        asyncio.run(live_context.build_race_forecast_context_tool(object())(TODAY))
    assert read.await_count == 1


@pytest.mark.parametrize("code,day,horizon", [(TODAY, "2024-01-06", "today"), (TOMORROW, "2024-01-07", "tomorrow")])
def test_context_infers_day_and_keeps_current_facts_separate_from_market(
    monkeypatch: pytest.MonkeyPatch, code: str, day: str, horizon: str
) -> None:
    monkeypatch.setattr(boundary_module, "now_jst", lambda: datetime(2024, 1, 6, 12, tzinfo=JST))
    monkeypatch.setattr(
        boundary_module,
        "fetch_rows",
        AsyncMock(
            return_value=[
                {
                    "race_date": day,
                    "scheduled_start_at": day + "T15:00:00",
                }
            ]
        ),
    )
    base = RaceForecastContextOutput(race_code=code, race_date=day, race={}, entries=[], facts={}, horses=[])
    rows = [
        {
            "jockey_id": "J",
            "jockey_name": "Jockey",
            "race_id": "earlier",
            "horse_id": "h",
            "finish_position": 2,
            "is_finisher": True,
            "is_final_result": False,
        },
        {"jockey_id": "K", "jockey_name": "No records", "race_id": None},
    ]

    async def read(_db: object, sql: str, params: dict[str, object], **kwargs: object) -> list[dict[str, object]]:
        if sql == live_context._JOCKEY_DAY_SQL:
            assert params["today"] == "2024-01-06" and params["race_code"] == code
            return rows
        return []

    monkeypatch.setattr(live_context, "fetch_rows", read)
    from jvdata_bridge_mcp.tools.prediction import _speed_read_model

    monkeypatch.setattr(
        _speed_read_model,
        "fetch_rows",
        AsyncMock(return_value=[{"history_before_date": day, "target_horses": 2, "available_horses": 0}]),
    )
    monkeypatch.setattr(live_context, "build_race_facts_context_tool", lambda _db: AsyncMock(return_value=base))
    out = asyncio.run(live_context.build_race_forecast_context_tool(object())(code, sections=["history"]))
    assert not out.model_availability.speed_quality.ready
    assert out.model_availability.speed_quality.missing_or_invalid_entries == 2
    assert out.current_entry_observations.history_entries_removed is False
    assert out.horizon == horizon and out.race_context == base
    assert out.same_day_evidence_already_in_base is False
    first, no_records = out.today_jockey_results
    assert first["starts"] == first["top3"] == first["provisional_starts"] == 1
    assert first["condition_inference"].startswith("unassessed")
    assert no_records["starts"] == 0 and no_records["win_rate"] is None
    assert not out.target_results_included and not out.market_included
    assert out.boundary.history_before_at == "2024-01-06T12:00:00+09:00"
