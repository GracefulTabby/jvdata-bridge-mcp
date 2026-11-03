"""公開ツールの情報境界と出力の正規化を検証する。"""

from __future__ import annotations

import asyncio

import pytest

from jvdata_bridge_mcp.tools.odds.table import _SQL as FUKUSHO_SQL
from jvdata_bridge_mcp.tools.odds.table import _SQL as ODDS_SUMMARY_SQL
from jvdata_bridge_mcp.tools.odds.table import _SQL as TANSHO_SQL
from jvdata_bridge_mcp.tools.pace.beneficiaries import (
    _SQL as BENEFICIARIES_SQL,
)
from jvdata_bridge_mcp.tools.pace.beneficiaries import (
    RacePaceIdentifyBeneficiariesOutput,
    _stability,
)
from jvdata_bridge_mcp.tools.pace.jockey_tactics import _SQL as JOCKEY_TACTICS_SQL
from jvdata_bridge_mcp.tools.pace.running_styles import _SQL as RUNNING_STYLES_SQL
from jvdata_bridge_mcp.tools.pace.running_styles import (
    RacePaceRunningStylesOutput,
    _style_label,
    _style_stats,
)
from jvdata_bridge_mcp.tools.race.condition_changes import (
    _SQL as CONDITION_CHANGES_SQL,
)
from jvdata_bridge_mcp.tools.race.condition_changes import (
    RaceConditionChangeFactsOutput,
)
from jvdata_bridge_mcp.tools.race.evidence import (
    _SQL as EVIDENCE_SQL,
)
from jvdata_bridge_mcp.tools.race.evidence import (
    RaceEvidenceCollectorOutput,
    _make_item,
    _raw_running_time,
    _raw_time_difference,
)
from jvdata_bridge_mcp.tools.race.recent_form import _SQL as RECENT_FORM_SQL
from jvdata_bridge_mcp.tools.race.results import _SQL as RESULTS_SQL
from jvdata_bridge_mcp.tools.race.similar import _SQL as SIMILAR_SQL
from jvdata_bridge_mcp.tools.race.track_condition import _SQL as TRACK_CONDITION_SQL


def test_track_condition_excludes_target_day_values() -> None:
    assert "w.race_date=CAST(:today AS date)" in TRACK_CONDITION_SQL
    assert "w.announced_at<=CAST(:analysis_local AS timestamp)" in TRACK_CONDITION_SQL
    assert "w.race_date AS observation_date" in TRACK_CONDITION_SQL
    assert "r.weather_name" not in TRACK_CONDITION_SQL


def test_results_exclude_nonstarters_and_recent_history_keeps_declared_entries() -> None:
    """結果側は実出走、予想側は出馬表の全馬の過去実出走を使う。"""

    assert "rr.is_final_result IS TRUE" in RESULTS_SQL
    assert "rr.is_starter IS TRUE" in RESULTS_SQL
    assert "rr.is_finisher IS TRUE" in RESULTS_SQL
    assert "rr.is_final_result IS TRUE" in EVIDENCE_SQL
    assert "JOIN jv_mart.horse_runs h" in RECENT_FORM_SQL
    assert "LEFT JOIN jv_mart.horse_runs h" not in RECENT_FORM_SQL
    assert "h.is_final_result IS TRUE" in RECENT_FORM_SQL
    assert "h.is_starter IS TRUE" in RECENT_FORM_SQL
    assert "run_number IS NULL" not in RECENT_FORM_SQL
    assert "c.abnormality_name" not in RECENT_FORM_SQL


def test_similar_races_keep_the_same_venue_and_track() -> None:
    """同距離・同クラスでも競馬場や芝ダートを跨いだ候補を返さない。"""

    assert "r.venue_code = t.venue_code" in SIMILAR_SQL
    assert "r.track_code = t.track_code" in SIMILAR_SQL
    assert "COALESCE(r.starters, 0) > 0" in SIMILAR_SQL
    assert "COALESCE(r.starters, r.registered_horses, 0)" in SIMILAR_SQL


def test_odds_tables_separate_source_observation_from_latest_runner_status() -> None:
    for statement in (TANSHO_SQL, FUKUSHO_SQL, ODDS_SUMMARY_SQL):
        assert "o.artifact_hash=s.artifact_hash" in statement
        assert "abnormality_code" not in statement


@pytest.mark.parametrize("bet_type", ["win", "place"])
def test_odds_tables_preserve_rows_and_states_when_every_row_is_inactive(
    bet_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime
    from unittest.mock import AsyncMock

    from jvdata_bridge_mcp.tools import _observation_scope as temporal
    from jvdata_bridge_mcp.tools.odds import table

    monkeypatch.setattr(temporal, "now_jst", lambda: datetime(2024, 1, 6, 12, tzinfo=temporal.JST))
    monkeypatch.setattr(
        table,
        "fetch_rows",
        AsyncMock(
            return_value=[
                {
                    "combination": f"{n:02d}",
                    "horse_number_1": n,
                    "odds_status": "cancelled_before_sales",
                    "snapshot_at": "2024-01-06T11:55:00",
                    "matching_row_count": 16,
                    "snapshot_row_count": 16,
                }
                for n in range(1, 17)
            ]
        ),
    )
    output = asyncio.run(table.build_odds_table_tool(object())("2024010605010111", bet_type))
    assert len(output.items) == output.snapshot_row_count == 16
    assert all(item.odds is None and item.odds_status == "cancelled_before_sales" for item in output.items)


def test_pace_tools_preserve_declared_target_evidence() -> None:
    """最新取消の時刻が不明でも過去の脚質・騎手事実を失わない。"""

    for statement in (RUNNING_STYLES_SQL, BENEFICIARIES_SQL, JOCKEY_TACTICS_SQL):
        assert "c.abnormality_code" not in statement
        assert "c.abnormality_name" not in statement

    assert "h.is_starter IS TRUE" in RUNNING_STYLES_SQL
    assert "h.is_starter IS TRUE" in BENEFICIARIES_SQL
    assert "rr.is_starter IS TRUE" in JOCKEY_TACTICS_SQL


def test_nested_core_models_expose_authoritative_semantic_shapes() -> None:
    """core出力は、事実の意味に対応する入れ子構造を持つ。"""

    assert {"race_code", "race_context", "items", "count"} <= set(RaceConditionChangeFactsOutput.model_fields)
    assert {"race_code", "laps", "items", "count"} <= set(RaceEvidenceCollectorOutput.model_fields)
    assert {
        "race_code",
        "race_name",
        "closers",
        "front_runners",
        "positional_specialists",
        "uncertain_style_horses",
        "count",
    } <= set(RacePaceIdentifyBeneficiariesOutput.model_fields)
    assert "changes" not in RaceConditionChangeFactsOutput.model_fields
    assert "競走条件コード 2歳条件" in CONDITION_CHANGES_SQL
    assert "競走条件コード 5歳以上条件" in CONDITION_CHANGES_SQL


def test_evidence_distinguishes_did_not_finish_from_withdrawal_and_missing_result() -> None:
    """発走後の競走中止を取消や単なる未確定に丸めず、集計対象外にする。"""

    common: dict[str, object] = {
        "horse_number": 14,
        "horse_name": "チャーミングアイ",
        "finish_position": None,
        "elapsed_seconds": None,
        "time_difference_seconds": None,
        "last_3f": None,
        "corner_1_position": None,
        "corner_2_position": None,
        "corner_3_position": None,
        "corner_4_position": None,
    }
    did_not_finish = _make_item(
        {
            **common,
            "abnormality_code": "4",
            "abnormality_name": "競走中止",
            "is_starter": True,
            "is_finisher": False,
        }
    )
    withdrawn = _make_item(
        {
            **common,
            "abnormality_code": "1",
            "abnormality_name": "出走取消",
            "is_starter": False,
            "is_finisher": False,
        }
    )
    not_confirmed = _make_item(
        {
            **common,
            "abnormality_code": None,
            "abnormality_name": None,
            "is_starter": None,
            "is_finisher": None,
        }
    )

    assert did_not_finish.finish_position.status == "did_not_finish"
    assert did_not_finish.finish_position.excluded_from_aggregates is True
    assert did_not_finish.finish_position.excluded_reason == "did_not_finish"
    assert withdrawn.finish_position.status == "withdrawn"
    assert withdrawn.finish_position.excluded_from_aggregates is True
    assert not_confirmed.finish_position.status == "not_confirmed"
    assert not_confirmed.finish_position.excluded_from_aggregates is True


def test_evidence_raw_times_preserve_jra_minute_second_encoding() -> None:
    """秒単位の時計をMMSS/十分の一秒表記へ変換する。"""

    assert _raw_running_time(152.0) == "2320"
    assert _raw_running_time(184.8) == "3048"
    assert _raw_time_difference(0.7) == "+007"
    assert _raw_time_difference(-1.2) == "-012"


def test_beneficiary_stability_uses_correlation_scale() -> None:
    """騎手位置安定度は相関係数と、その閾値によるラベルを返す。"""

    assert "first_recorded_corner_position" in BENEFICIARIES_SQL
    assert "last_recorded_corner_position" in BENEFICIARIES_SQL
    assert "first_recorded_corner_position - last_recorded_corner_position" in BENEFICIARIES_SQL
    assert "finish_position - corner_4_position" not in BENEFICIARIES_SQL
    assert _stability(None) == (None, "不明")
    assert _stability(0.3) == (0.3, "低")
    assert _stability(0.8) == (0.8, "高")


def test_running_styles_report_forward_position_gain() -> None:
    """脚質別の順位変化は正=前進、負=後退として公開する。"""

    assert {"race_code", "items", "count", "style_method", "style_version", "result_policy"} == set(
        RacePaceRunningStylesOutput.model_fields
    )
    assert "styles" not in RacePaceRunningStylesOutput.model_fields
    assert "first_recorded_corner_position" not in RUNNING_STYLES_SQL
    assert "h.corner_2_position" in RUNNING_STYLES_SQL
    assert "h.corner_1_position" in RUNNING_STYLES_SQL
    assert "h.corner_4_position" in RUNNING_STYLES_SQL
    assert "hd.finish_position <= 3" in RUNNING_STYLES_SQL
    assert "distance_stats.top3 > 0" in RUNNING_STYLES_SQL
    assert _style_label("front_runner") == "逃げ"
    assert _style_label("deep_closer") == "追込"
    assert _style_label(None) == "不明"
    assert _style_stats(
        {
            "closer": {
                "starts": 4,
                "wins": 1,
                "seconds": 1,
                "thirds": 0,
                "win_rate": 25.0,
                "place_rate": 50.0,
                "show_rate": 50.0,
            }
        }
    ) == {
        "差し": {
            "総数": 4,
            "1着": 1,
            "2着": 1,
            "3着": 0,
            "勝率": 25.0,
            "連対率": 50.0,
            "複勝率": 50.0,
        }
    }
