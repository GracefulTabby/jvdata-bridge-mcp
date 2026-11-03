"""当日情報を失っていた境界・差分通知・速報の回帰試験。"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from jvdata_bridge_mcp.tools import _observation_scope as temporal
from jvdata_bridge_mcp.tools.odds import table
from jvdata_bridge_mcp.tools.race import search
from jvdata_bridge_mcp.tools.race._ledger import runner_status
from jvdata_bridge_mcp.tools.race._weather import reconstruct_weather
from jvdata_bridge_mcp.tools.race.evidence import _make_item
from jvdata_bridge_mcp.tools.race.live_context import _summarize


@pytest.fixture
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    now = datetime(2024, 1, 6, 14, 30, tzinfo=temporal.JST)
    monkeypatch.setattr(temporal, "now_jst", lambda: now)
    return now


@pytest.mark.parametrize("day", ["20240106", "20240107"])
def test_observations_infer_current_boundary_from_target(fixed_now: datetime, day: str) -> None:
    params = temporal.observation_parameters(day + "05040611")
    assert params["observation_scope"] == "live"
    assert params["analysis_local"] == "2024-01-06T14:30:00"


def test_observations_reject_historical_replay(fixed_now: datetime) -> None:
    with pytest.raises(ValueError, match="historical replay"):
        temporal.observation_parameters("2024010505010111")
    assert "available_at<=" not in table._SQL


def test_weather_only_change_preserves_both_ground_conditions_and_their_provenance() -> None:
    initial = {
        "observation_date": "2024-01-06",
        "announced_at": "2024-01-06T00:00:00",
        "change_type_code": "1",
        "current_weather_code": "2",
        "current_weather_name": "曇",
        "current_turf_condition_code": "1",
        "current_turf_condition_name": "良",
        "current_dirt_condition_code": "2",
        "current_dirt_condition_name": "稍重",
    }
    change = {
        **initial,
        "announced_at": "2024-01-06T06:55:00",
        "change_type_code": "2",
        "current_weather_code": "1",
        "current_weather_name": "晴",
        "current_turf_condition_code": "0",
        "current_turf_condition_name": "未設定",
        "current_dirt_condition_code": "0",
        "current_dirt_condition_name": "未設定",
    }
    # Input order does not replace the publication order.
    out = reconstruct_weather([change, initial])
    assert (out["current_weather_name"], out["current_turf_condition_name"], out["current_dirt_condition_name"]) == (
        "晴",
        "良",
        "稍重",
    )
    assert out["observation_status"] == "complete"
    assert out["field_observations"]["turf_condition"]["announced_at"] == initial["announced_at"]
    assert out["field_observations"]["weather"]["announced_at"] == change["announced_at"]
    ground = {
        **change,
        "announced_at": "2024-01-06T12:00:00",
        "current_weather_code": "0",
        "current_turf_condition_code": "3",
        "current_turf_condition_name": "重",
    }
    assert reconstruct_weather([initial, change, ground])["current_turf_condition_name"] == "重"


def test_weather_does_not_carry_ground_from_another_day_or_expose_zero_as_a_condition() -> None:
    rows = [
        {"observation_date": "2024-01-05", "current_turf_condition_code": "1", "current_turf_condition_name": "良"},
        {
            "observation_date": "2024-01-06",
            "current_turf_condition_code": "0",
            "current_turf_condition_name": "未設定",
        },
    ]
    out = reconstruct_weather(rows)
    assert out["current_turf_condition_code"] is None and out["current_turf_condition_name"] is None
    assert out["observation_status"] == "unavailable"


def test_weather_same_minute_updates_use_numeric_source_order() -> None:
    row = {
        "observation_date": "2024-01-06",
        "announced_at": "2024-01-06T08:00:00",
        "current_turf_condition_code": "1",
        "current_turf_condition_name": "良",
        "source_sequence": 9,
    }
    updated = {**row, "current_turf_condition_code": "2", "current_turf_condition_name": "稍重", "source_sequence": 10}
    assert reconstruct_weather([updated, row])["current_turf_condition_code"] == "2"


def test_training_summary_resolves_every_cte_column_against_the_source_contract() -> None:
    import sqlglot
    from sqlglot.optimizer.qualify import qualify

    from jvdata_bridge_mcp.tools.training.condition_summary import _SQL

    schema = {
        "jv_data": {"races": {"race_id": "TEXT", "race_date": "DATE", "scheduled_start_at": "TIMESTAMP"}},
        "jv_mart": {
            "race_cards": {key: "TEXT" for key in ("race_id", "horse_number", "horse_id", "horse_name")},
            "training_sessions": {
                key: "TEXT"
                for key in (
                    "horse_id",
                    "training_date",
                    "training_time",
                    "training_type",
                    "training_center_code",
                    "course_code",
                    "quality_status",
                    "four_furlongs_seconds",
                    "source_sequence",
                )
            },
        },
    }
    # The former query omitted center/course in two intermediate projections and cannot qualify.
    qualify(sqlglot.parse_one(_SQL, read="postgres"), dialect="postgres", schema=schema)


def test_complete_provisional_results_are_visible_but_never_claim_official_finality() -> None:
    row = {
        "jockey_id": "J",
        "jockey_name": "Jockey",
        "race_id": "2024010605010101",
        "horse_id": "h",
        "is_finisher": True,
        "is_final_result": False,
        "is_complete_result": True,
        "result_status_code": "provisional_complete",
        "result_stage_code": "6",
        "finish_position": 2,
    }
    out = _summarize([row])[0]
    assert out["starts"] == 1 and out["top3"] == 1 and out["provisional_starts"] == 1
    assert out["runs"][0]["is_final_result"] is False
    assert runner_status(row) == "provisional_finished"
    fact = _make_item({**row, "horse_number": 1})
    assert fact.finish_position.value == 2 and fact.finish_position.status == "provisional"
    assert not fact.is_final_result
    empty = _summarize([{"jockey_id": "K", "race_id": None, "earlier_recorded_entries": 3}])[0]
    assert empty["starts"] == 0 and empty["win_rate"] is None
    assert empty["result_coverage_status"] == "no_completed_results_available"
    assert empty["earlier_recorded_entries"] == 3


def test_combination_format_respects_ordered_and_unordered_bets() -> None:
    assert table.normalize_combinations("trio", ["7-3-6", "030607"]) == ["030607"]
    assert table.normalize_combinations("exacta", ["7-3"]) == ["0703"]
    assert table.normalize_combinations("bracket_quinella", ["2-1", "11"]) == ["11", "12"]
    for bad in (["1-1-2"], ["1-2-19"], ["３-６-７"], ["1-2"]):
        with pytest.raises(ValueError):
            table.normalize_combinations("trio", bad)


def test_filtered_odds_keep_snapshot_identity_and_report_absent_tickets(
    monkeypatch: pytest.MonkeyPatch,
    fixed_now: datetime,
) -> None:
    read = AsyncMock(
        return_value=[
            {
                "combination": "030607",
                "odds": 8.4,
                "odds_status": "available",
                "snapshot_at": "2024-01-06T14:21:00",
                "data_stage_code": "1",
                "source_artifact_hash": "hash",
                "snapshot_row_count": 84,
                "matching_row_count": 1,
            }
        ]
    )
    monkeypatch.setattr(table, "fetch_rows", read)
    out = asyncio.run(table.build_odds_table_tool(object())("2024010605010111", "trio", ["3-6-7", "2-6-7"]))
    assert out.snapshot_at == "2024-01-06T14:21:00" and out.source_artifact_hash == "hash"
    assert out.observation_scope == "live" and out.pre_race_verified
    assert out.missing_combinations == ["020607"] and not out.truncated
    assert out.source_timing.age_seconds == 540 and out.source_timing.age_status == "aged"
    assert "source_retrieved_at" not in out.model_dump()
    assert out.items[0].odds == 8.4  # Age is displayed, not used to discard a valid stored snapshot.
    assert read.await_args.args[2]["combinations"] == ["020607", "030607"]


def test_no_matching_combinations_does_not_discard_the_available_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    fixed_now: datetime,
) -> None:
    monkeypatch.setattr(
        table,
        "fetch_rows",
        AsyncMock(
            return_value=[
                {
                    "snapshot_at": "2024-01-06T14:21:00",
                    "combination": None,
                    "snapshot_row_count": 84,
                    "matching_row_count": 0,
                }
            ]
        ),
    )
    out = asyncio.run(table.build_odds_table_tool(object())("2024010605010111", "trio", ["3-6-7"]))
    assert out.pre_race_verified and out.items == [] and out.missing_combinations == ["030607"]
    assert out.missing_reason == "requested_combinations_missing"


def test_today_search_does_not_require_guessing_race_codes(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(search, "now_jst", lambda: fixed_now)
    read = AsyncMock(return_value=[])
    monkeypatch.setattr(search, "fetch_rows", read)
    asyncio.run(search.build_search_races_tool(object())(upcoming_only=True, grade="G2"))
    params = read.await_args.args[2]
    assert params["race_date"] == "2024-01-06" and params["upcoming_only"]
    assert params["grade"] == "B"
    assert "r.scheduled_start_at" in read.await_args.args[1]
