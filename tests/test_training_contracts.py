"""直前21日調教4ツールの公開shape・PIT境界・サンプル不足回帰試験。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from jvdata_bridge_mcp.tools.training import condition_summary, entry_detail, evaluation, intensity
from jvdata_bridge_mcp.tools.training._normalization import relative_training_scores

RACE_1 = "2026082307030212"
RACE_2 = "2024122206050811"


def test_training_sql_uses_the_same_pre_race_window_and_quality_boundary() -> None:
    """4ツールが品質有効な直前21日と時刻の分かる先行同日分を読む。"""

    for statement in (
        evaluation._SQL,
        condition_summary._SQL,
        entry_detail._SQL,
        intensity._SQL,
    ):
        assert "training_date >=" in statement
        assert "training_date <" in statement
        assert "s.training_time IS NOT NULL" in statement
        assert "scheduled_start_at" in statement
        assert "quality_status IN ('ok', 'valid')" in statement
        assert "c.abnormality_code" not in statement
        assert "c.abnormality_name" not in statement

    assert "INTERVAL '21 days'" in evaluation._SQL
    assert "INTERVAL '21 days'" in condition_summary._SQL
    assert "INTERVAL '21 days'" in entry_detail._SQL
    assert "INTERVAL '21 days'" in intensity._SQL
    for statement in (evaluation._SQL, condition_summary._SQL):
        assert "latest_type AS MATERIALIZED" in statement
        assert "comparable_rows AS MATERIALIZED" in statement
        assert "IS NOT DISTINCT FROM" in statement


def test_relative_training_scores_exclude_bad_quality_and_implausible_4f() -> None:
    """スコアは品質不良・物理境界外の時計で水増しされない。"""

    rows: list[Mapping[str, object]] = [
        {"quality_status": "valid", "training_type": "坂路", "four_furlongs_seconds": 60.0},
        {"quality_status": "valid", "training_type": "坂路", "four_furlongs_seconds": 62.0},
        {"quality_status": "valid", "training_type": "坂路", "four_furlongs_seconds": 64.0},
        {"quality_status": "invalid", "training_type": "坂路", "four_furlongs_seconds": 35.0},
        {"quality_status": "valid", "training_type": "坂路", "four_furlongs_seconds": 101.0},
        {"quality_status": "valid", "training_type": "ウッド", "four_furlongs_seconds": 80.0},
    ]

    condition, trend, count = relative_training_scores(rows)

    assert condition == 7.4
    assert trend == 8.7
    assert count == 3
    assert relative_training_scores([rows[3]]) == (None, None, 0)


def test_evaluation_maps_internal_scores_to_descriptive_rank(monkeypatch: pytest.MonkeyPatch) -> None:
    """調教評価は記述的な自己内相対値として公開する。"""

    async def fake_fetch_rows(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "horse_number": 1,
                "horse_id": "H001",
                "horse_name": "テストホース",
                "valid_four_furlongs_count": 4,
                "training_center": "美浦",
                "condition_score": 8.0,
                "trend_score": 8.0,
            }
        ]

    monkeypatch.setattr(evaluation, "fetch_rows", fake_fetch_rows)
    output = asyncio.run(evaluation.build_race_training_evaluation_tool(object())(RACE_1))

    assert output.race_code == RACE_1
    assert output.count == 1
    assert output.items[0].horse_number == "01"
    assert output.items[0].rank == "A"
    assert "有効4F 4本" in output.items[0].rank_reason
    assert set(output.model_dump()) == {"race_code", "items", "count"}


def test_condition_summary_does_not_turn_missing_scores_into_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """比較不能馬を0点へ変換せず、分布の母数から除外する。"""

    async def fake_fetch_rows(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "horse_number": 1,
                "horse_id": "H001",
                "horse_name": "比較可",
                "valid_four_furlongs_count": 3,
                "condition_score": 8.0,
                "trend_score": 8.0,
            },
            {
                "horse_number": 2,
                "horse_id": "H002",
                "horse_name": "比較不可",
                "valid_four_furlongs_count": 0,
                "condition_score": None,
                "trend_score": None,
            },
        ]

    monkeypatch.setattr(condition_summary, "fetch_rows", fake_fetch_rows)
    output = asyncio.run(condition_summary.build_training_condition_summary_tool(object())(RACE_1))

    assert output.distribution["scored_entries"] == 1
    assert output.average_score == 8.0
    assert output.low_performers == []


def test_condition_summary_separates_raw_components_from_combined_candidate_score() -> None:
    """候補の順位用スコアとcondition/trendの各成分を明示する。"""

    output = condition_summary._output(
        RACE_1,
        [
            {
                "horse_number": 1,
                "horse_id": "H001",
                "horse_name": "候補馬",
                "valid_four_furlongs_count": 4,
                "condition_score": 9.0,
                "trend_score": 7.0,
            }
        ],
        5,
    )

    candidate = output.high_performers[0]
    assert "condition_score" not in candidate
    assert candidate["combined_score"] == 8.0
    assert candidate["raw_condition_score"] == 9.0
    assert candidate["trend_score"] == 7.0
    assert output.selection["score_field"] == "combined_score"


def test_entry_detail_returns_recent_history_and_valid_best_times(monkeypatch: pytest.MonkeyPatch) -> None:
    """指定馬は直近履歴と妥当な最良時計を返し、null sentinel行を作らない。"""

    async def fake_fetch_rows(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "horse_number": 1,
                "horse_id": "H001",
                "horse_name": "テストホース",
                "training_date": "2026-08-20",
                "training_time": "08:00:00",
                "training_center_code": "MIHO",
                "course_code": "坂路",
                "direction_code": "右",
                "training_type": "坂路",
                "four_furlongs_seconds": 60.0,
                "three_furlongs_seconds": 45.0,
                "two_furlongs_seconds": 30.0,
                "lap_200_0_seconds": 15.0,
                "quality_status": "valid",
            },
            {
                "horse_number": 1,
                "horse_id": "H001",
                "horse_name": "テストホース",
                "training_date": "2026-08-16",
                "training_time": "08:00:00",
                "training_center_code": "MIHO",
                "course_code": "坂路",
                "direction_code": "右",
                "training_type": "坂路",
                "four_furlongs_seconds": 62.0,
                "three_furlongs_seconds": 47.0,
                "two_furlongs_seconds": 31.0,
                "lap_200_0_seconds": 16.0,
                "quality_status": "valid",
            },
        ]

    monkeypatch.setattr(entry_detail, "fetch_rows", fake_fetch_rows)
    output = asyncio.run(entry_detail.build_training_entry_detail_tool(object())("01", RACE_1))

    assert output.horse_number == "01"
    assert output.condition_score is not None
    assert output.best_times["f4"]["time"] == 60.0
    assert len(output.recent_history) == 2


def test_intensity_keeps_active_cards_with_no_valid_sessions_as_null(monkeypatch: pytest.MonkeyPatch) -> None:
    """active馬は一覧に残すが、調教がなければ一律lowではなくnullにする。"""

    async def fake_fetch_rows(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "horse_number": 1,
                "horse_id": "H001",
                "horse_name": "調教あり",
                "training_date": "2026-08-20",
                "training_type": "坂路",
                "quality_status": "valid",
                "four_furlongs_seconds": 60.0,
            },
            {"horse_number": 2, "horse_id": "H002", "horse_name": "調教なし", "training_date": None},
        ]

    monkeypatch.setattr(intensity, "fetch_rows", fake_fetch_rows)
    output = asyncio.run(intensity.build_training_intensity_analysis_tool(object())(RACE_2))

    assert output.count == 2
    assert output.items[0].session_count == 1
    assert output.items[0].intensity_score is not None
    assert output.items[1].session_count == 0
    assert output.items[1].intensity_score is None
    assert output.items[1].intensity_label is None
