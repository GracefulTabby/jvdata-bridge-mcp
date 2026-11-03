"""read modelの実値を固定ツールの正規化へ反映する回帰試験。"""

from __future__ import annotations

import pytest

from jvdata_bridge_mcp.tools.bloodline.scoreboard import _SQL as SCOREBOARD_SQL
from jvdata_bridge_mcp.tools.course.frame_style_stats import _SQL as TRACK_BIAS_SQL
from jvdata_bridge_mcp.tools.jockey.recent_form import _SQL as JOCKEY_RECENT_FORM_SQL
from jvdata_bridge_mcp.tools.misc.composite import (
    _SQL as COMPOSITE_SQL,
)
from jvdata_bridge_mcp.tools.misc.composite import (
    CompositeAnalysisItem,
)
from jvdata_bridge_mcp.tools.odds.table import _SQL as FUKUSHO_SQL
from jvdata_bridge_mcp.tools.odds.table import _SQL as TANSHO_SQL
from jvdata_bridge_mcp.tools.pace._normalization import normalize_running_style
from jvdata_bridge_mcp.tools.pace.beneficiaries import _SQL as BENEFICIARIES_SQL
from jvdata_bridge_mcp.tools.pace.jockey_tactics import _SQL as JOCKEY_TACTICS_SQL
from jvdata_bridge_mcp.tools.pace.running_styles import RunningStyleItem
from jvdata_bridge_mcp.tools.prediction._indicator_sql import DEFAULT_INDICATOR_WEIGHTS
from jvdata_bridge_mcp.tools.prediction.features import _SQL as PREDICTION_FEATURES_SQL
from jvdata_bridge_mcp.tools.prediction.probabilities import _SQL as PREDICTION_PROBABILITIES_SQL
from jvdata_bridge_mcp.tools.prediction.probabilities import RaceProbabilityItem
from jvdata_bridge_mcp.tools.prediction.scenarios import (
    _SQL as PREDICTION_SCENARIOS_SQL,
)
from jvdata_bridge_mcp.tools.prediction.scenarios import (
    RaceScenarioItem,
)
from jvdata_bridge_mcp.tools.training._normalization import is_plausible_four_furlongs_seconds
from jvdata_bridge_mcp.tools.training.evaluation import _SQL as TRAINING_EVALUATION_SQL
from jvdata_bridge_mcp.tools.training.intensity import _SQL as TRAINING_INTENSITY_SQL


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", "front_runner"),
        ("2", "stalker"),
        ("3", "closer"),
        ("4", "deep_closer"),
        ("逃げ", "front_runner"),
        ("追込", "deep_closer"),
        ("0", "unknown"),
        (None, "unknown"),
    ],
)
def test_running_style_values_are_normalized(value: str | None, expected: str) -> None:
    """JRAの数値脚質コードと既存別名を共通ラベルへ変換する。"""

    assert normalize_running_style(value) == expected


def test_odds_tables_read_one_prior_day_market_observation() -> None:
    for statement in (TANSHO_SQL, FUKUSHO_SQL):
        assert "FROM jv_mart.odds_history_occurrences" in statement
        assert "o.artifact_hash=s.artifact_hash" in statement
        assert "published_at<=CAST(:analysis_local AS timestamp)" in statement


def test_pedigree_queries_use_relation_code_and_canonical_parent_sides() -> None:
    """親系統の表示側ではなく、祖先との関係を表す実コードで絞り込む。"""

    assert "relation_code" in SCOREBOARD_SQL
    assert "parent_side IN ('sire'" not in SCOREBOARD_SQL
    assert "parent_side IN ('dam'" not in SCOREBOARD_SQL


def test_running_style_aggregations_use_the_shared_canonical_groups() -> None:
    """数値コードでも前方・差し追込の集計がゼロにならないようにする。"""

    assert "front_runner" in JOCKEY_TACTICS_SQL
    assert "deep_closer" in JOCKEY_TACTICS_SQL

    assert "main_style" in RunningStyleItem.model_fields
    assert "ketto_toroku_bango" in RunningStyleItem.model_fields
    assert "MODE() WITHIN GROUP" in BENEFICIARIES_SQL
    assert "history.common_running_style" in BENEFICIARIES_SQL


def test_historical_training_and_pace_inputs_stop_at_the_target_race() -> None:
    """後日の調教・過去走が対象レースの分析に混ざらない。"""

    for statement in (TRAINING_INTENSITY_SQL, TRAINING_EVALUATION_SQL):
        assert "training_date >=" in statement
        assert "training_date <" in statement
        assert "s.training_time IS NOT NULL" in statement
        assert "training_date + CAST(s.training_time AS time)" in statement
    assert "h.race_id <> :race_code" in BENEFICIARIES_SQL
    assert "e.race_id <> :race_code" in JOCKEY_TACTICS_SQL
    assert "r.race_id <> :race_code" in TRACK_BIAS_SQL
    assert "result.running_style_code" in JOCKEY_RECENT_FORM_SQL


@pytest.mark.parametrize(
    ("value", "expected"),
    [(34.9, False), (35.0, True), (60.0, True), (100.0, True), (100.1, False), (None, False)],
)
def test_training_four_furlong_clock_uses_a_plausible_range(value: float | None, expected: bool) -> None:
    """境界外の時計を現行の分析用4F時計へ混ぜない。"""

    assert is_plausible_four_furlongs_seconds(value) is expected


def test_training_aggregates_filter_invalid_clocks() -> None:
    """現行の調教集計は有効品質と4F時計の妥当性条件を使う。"""

    for statement in (TRAINING_INTENSITY_SQL, TRAINING_EVALUATION_SQL):
        assert "quality_status IN ('ok', 'valid')" in statement
    for statement in (TRAINING_EVALUATION_SQL,):
        assert "BETWEEN 35.0 AND 100.0" in statement


def test_prediction_tools_use_event_time_and_never_read_the_target_race_result() -> None:
    """過去走はイベント時刻で切り、同一レースの確定結果を予測入力にしない。"""

    for statement in (PREDICTION_FEATURES_SQL, PREDICTION_PROBABILITIES_SQL, PREDICTION_SCENARIOS_SQL):
        assert "historical_cutoff_date" in statement
        assert "as_of_datetime" not in statement

    assert "available_at <= CAST(:as_of_datetime AS timestamptz)" not in PREDICTION_FEATURES_SQL
    assert "available_at <= CAST(:as_of_datetime AS timestamptz)" not in PREDICTION_PROBABILITIES_SQL
    assert "h.race_date < c.historical_cutoff_date" in PREDICTION_SCENARIOS_SQL
    assert "h.race_id = c.race_id" not in PREDICTION_SCENARIOS_SQL
    assert "uniform_fallback_no_history" not in PREDICTION_PROBABILITIES_SQL
    assert "raw_score / NULLIF(total_raw_score, 0)" in PREDICTION_PROBABILITIES_SQL
    assert "temporal_linear_v3_speed_horse_prior" in PREDICTION_PROBABILITIES_SQL
    assert "history_reliability" in RaceProbabilityItem.model_fields
    assert "lightly_raced_score" not in RaceProbabilityItem.model_fields
    for field in (
        "expected_front_runner_count",
        "expected_forward_runner_count",
        "style_coverage_rate",
        "pace_pressure_index",
    ):
        assert field in RaceScenarioItem.model_fields
    assert "fast_pace" in PREDICTION_SCENARIOS_SQL
    assert "slow_pace" in PREDICTION_SCENARIOS_SQL


def test_composite_score_has_only_data_derived_weighted_components() -> None:
    """重みを定数加算せず、対象日以前の実データ由来コンポーネントだけを合成する。"""

    assert "h.race_date < c.historical_cutoff_date" in COMPOSITE_SQL
    assert "history_reliability_raw AS history_reliability" in COMPOSITE_SQL
    for weight in (
        "aptitude_weight",
        "recent_form_weight",
        "distance_aptitude_weight",
        "course_aptitude_weight",
        "closing_weight",
        "time_quality_weight",
        "historical_market_weight",
        "jockey_form_weight",
        "trainer_form_weight",
        "horse_jockey_form_weight",
    ):
        assert f"* :{weight}" in COMPOSITE_SQL
        assert f"+ :{weight}" not in COMPOSITE_SQL

    for field in (
        "aptitude_score",
        "recent_form_score",
        "distance_aptitude_score",
        "course_aptitude_score",
        "closing_score",
        "time_quality_score",
        "historical_market_score",
        "jockey_form_score",
        "trainer_form_score",
        "horse_jockey_form_score",
    ):
        assert field in CompositeAnalysisItem.model_fields
    assert "lightly_raced_score" not in CompositeAnalysisItem.model_fields
    assert "assigned_weight_kg *" not in COMPOSITE_SQL
    assert set(DEFAULT_INDICATOR_WEIGHTS) == {
        "aptitude_weight",
        "recent_form_weight",
        "distance_aptitude_weight",
        "course_aptitude_weight",
        "closing_weight",
        "time_quality_weight",
        "historical_market_weight",
        "jockey_form_weight",
        "trainer_form_weight",
        "horse_jockey_form_weight",
    }
    assert all(weight > 0.0 for weight in DEFAULT_INDICATOR_WEIGHTS.values())
