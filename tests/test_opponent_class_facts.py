"""近走詳細と相手関係Factの境界・出力を検証する。"""

from __future__ import annotations

import asyncio

from jvdata_bridge_mcp.tools.race.opponent_class import (
    _RECORD_SQL,
    OpponentRecordBeforeTarget,
    _items_from_rows,
)
from jvdata_bridge_mcp.tools.race.opponent_class import (
    _SQL as OPPONENT_SQL,
)
from jvdata_bridge_mcp.tools.race.recent_form import _SQL as RECENT_FORM_SQL
from jvdata_bridge_mcp.tools.race.recent_form import build_race_recent_form_facts_tool


def test_recent_form_exposes_five_run_comparison_facts_without_target_results() -> None:
    """近走詳細は対象日より前の確定走だけを使う。"""

    for name in (
        "recent_class_name",
        "field_size",
        "elapsed_seconds",
        "time_difference_seconds",
        "last_three_furlongs_seconds",
        "corner_1_position",
        "corner_4_position",
    ):
        assert name in RECENT_FORM_SQL
    assert "h.race_id <> :race_code" in RECENT_FORM_SQL
    assert "h.is_complete_result IS TRUE" in RECENT_FORM_SQL
    assert "available_at" not in RECENT_FORM_SQL
    assert "CASE WHEN h.finish_position = 1 THEN 0.0" in RECENT_FORM_SQL

    async def call_with_defaults() -> None:
        from jvdata_bridge_mcp.tools.race import recent_form

        captured: dict[str, object] = {}
        original = recent_form.fetch_rows

        async def fake_fetch_rows(
            _database: object, _sql: str, parameters: dict[str, object]
        ) -> list[dict[str, object]]:
            captured.update(parameters)
            return []

        recent_form.fetch_rows = fake_fetch_rows
        try:
            await build_race_recent_form_facts_tool(object())("2026092609040811")
        finally:
            recent_form.fetch_rows = original
        assert captured["recent_runs"] == 5

    asyncio.run(call_with_defaults())


def test_opponent_class_facts_keep_past_race_and_target_boundaries() -> None:
    """対象レース結果や同日結果を相手関係へ混ぜない。"""

    assert "h.race_id <> :race_code" in OPPONENT_SQL
    assert "h.is_final_result IS TRUE" in OPPONENT_SQL
    assert "h.is_complete_result IS TRUE" in OPPONENT_SQL
    assert "peer.race_id = h.past_race_code" in OPPONENT_SQL
    assert "peer.horse_id <> h.horse_id" in OPPONENT_SQL
    assert "peer.finish_position BETWEEN 1 AND 3" in OPPONENT_SQL
    assert "peer.race_id = :race_code" not in OPPONENT_SQL
    assert "CASE WHEN peer.finish_position = 1 THEN 0.0" in OPPONENT_SQL
    assert "h.race_id <> :race_code" in _RECORD_SQL
    assert "h.horse_id = ANY(:opponent_ids)" in _RECORD_SQL
    assert "h.is_complete_result IS TRUE" in _RECORD_SQL


def test_opponent_facts_group_top_three_peers_without_inventing_strength_score() -> None:
    """同じ過去走を1件にまとめ、クラス不明や相手欠損をそのまま示す。"""

    base: dict[str, object] = {
        "horse_number": 4,
        "horse_name": "対象馬",
        "run_number": 1,
        "past_race_code": "2026052308030911",
        "past_race_date": "2026-05-23",
        "past_race_name": "過去の重賞",
        "class_name": "G3（平地競走）",
        "field_size": 16,
        "horse_finish_position": 11,
        "horse_time_difference_seconds": 1.8,
    }
    rows = [
        {
            **base,
            "opponent_horse_id": "horse-a",
            "opponent_horse_name": "勝馬",
            "opponent_finish_position": 1,
            "opponent_time_difference_seconds": 0.0,
        },
        {
            **base,
            "opponent_horse_id": "horse-b",
            "opponent_horse_name": "2着馬",
            "opponent_finish_position": 2,
            "opponent_time_difference_seconds": 0.6,
        },
        {
            **base,
            "run_number": 2,
            "past_race_code": "2026041809020711",
            "class_name": None,
            "opponent_horse_id": None,
            "opponent_horse_name": None,
            "opponent_finish_position": None,
            "opponent_time_difference_seconds": None,
        },
    ]
    items = _items_from_rows(
        rows,
        {"horse-a": OpponentRecordBeforeTarget(starts=16, wins=6, top3_finishes=11, graded_top3_finishes=6)},
    )
    assert len(items) == 2
    assert items[0].class_name == "GIII"
    assert items[0].class_rank == 70
    assert [peer.finish_position for peer in items[0].top_three_opponents] == [1, 2]
    assert items[0].top_three_opponents[0].record_before_target is not None
    assert items[0].top_three_opponents[0].record_before_target.graded_top3_finishes == 6
    assert items[0].top_three_opponents[1].record_before_target is None
    assert items[1].class_rank is None
    assert items[1].top_three_opponents == []
