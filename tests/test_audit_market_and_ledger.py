"""市場を分けた出馬表、同一観測オッズ、非完走状態を検証する。"""

import asyncio

import pytest

from jvdata_bridge_mcp.tools.odds import table
from jvdata_bridge_mcp.tools.race import entries
from jvdata_bridge_mcp.tools.race._ledger import runner_status


def test_market_free_entries_never_read_target_odds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def read(db: object, sql: str, parameters: object, **kwargs: object) -> list[dict[str, object]]:
        assert "final_win_odds" not in sql and "win_popularity" not in sql
        return [{"race_code": "2026092205040111", "horse_number": 1, "horse_id": "h", "horse_name": "H"}]

    monkeypatch.setattr(entries, "fetch_rows", read)
    out = asyncio.run(entries.build_entries_list_tool(object())("2026092205040111"))
    assert "final_win_odds" not in out.entries[0].model_dump()
    assert not out.market_fields_included


def test_market_snapshot_uses_one_published_prefinal_observation() -> None:
    sql = table._SQL
    assert "o.published_at=s.published_at" in sql and "o.artifact_hash=s.artifact_hash" in sql
    assert "data_stage_code IN ('1','2')" in sql
    assert "published_at<=CAST(:analysis_local AS timestamp)" in sql
    assert "available_at<=" not in sql
    assert "abnormality_code" not in sql


def test_dnf_and_disqualification_are_never_pre_race_states() -> None:
    assert runner_status({"abnormality_code": "4"}) == "did_not_finish"
    assert runner_status({"abnormality_code": "5"}) == "disqualified"
    assert runner_status({"abnormality_code": "4"}, pre_race=True) == "entry_listed"
    assert runner_status({"abnormality_code": "2"}, pre_race=True) == "excluded"


@pytest.mark.parametrize("expected", [[1], [1, 2]])
def test_coverage_cannot_claim_complete_when_a_listed_horse_is_absent(
    monkeypatch: pytest.MonkeyPatch, expected: list[int]
) -> None:
    from datetime import datetime
    from unittest.mock import AsyncMock

    from jvdata_bridge_mcp.tools import _observation_scope as temporal

    monkeypatch.setattr(temporal, "now_jst", lambda: datetime(2024, 1, 6, 12, tzinfo=temporal.JST))
    monkeypatch.setattr(
        table,
        "fetch_rows",
        AsyncMock(
            return_value=[
                {
                    "combination": "01",
                    "horse_number_1": 1,
                    "odds_status": "available",
                    "odds": 3.0,
                    "snapshot_at": "2024-01-06T11:55:00",
                    "snapshot_row_count": 1,
                    "matching_row_count": 1,
                    "expected_horse_numbers": expected,
                    "observed_horse_numbers": [1],
                }
            ]
        ),
    )
    out = asyncio.run(table.build_odds_table_tool(object())("2024010605010111", "win"))
    assert out.field_coverage_complete is (len(expected) == 1)
    assert out.missing_horse_numbers == expected[1:]


@pytest.mark.parametrize(
    "code,status,raw",
    [("1", "withdrawn", "取消"), ("2", "excluded", "除外"), ("3", "excluded", "除外"), ("5", "disqualified", "失格")],
)
def test_evidence_collector_keeps_distinct_administrative_states(code: str, status: str, raw: str) -> None:
    from jvdata_bridge_mcp.tools.race.evidence import _make_item

    out = _make_item({"horse_number": 1, "abnormality_code": code, "is_starter": code == "5", "is_finisher": False})
    assert out.finish_position.status == status
    assert out.finish_position.raw == raw
    assert out.finish_position.excluded_from_aggregates


def test_official_dnf_code_is_preserved_when_starter_flag_is_missing() -> None:
    from jvdata_bridge_mcp.tools.race.evidence import _make_item

    out = _make_item({"horse_number": 1, "abnormality_code": "4"})
    assert out.finish_position.status == "did_not_finish"
    assert out.finish_position.value is None
