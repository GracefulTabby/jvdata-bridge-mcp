"""重い分析ツールのクエリ形状が全表結合へ戻らないことを検証する。"""

from __future__ import annotations

from jvdata_bridge_mcp.tools.bloodline.distance_bias import _SQL as BLOODLINE_DISTANCE_BIAS_SQL
from jvdata_bridge_mcp.tools.bloodline.form_profile import _SQL as BLOODLINE_FORM_PROFILE_SQL
from jvdata_bridge_mcp.tools.course.advantage import _SQL as COURSE_ADVANTAGE_SQL
from jvdata_bridge_mcp.tools.course.frame_style_stats import _SQL as TRACK_BIAS_SQL
from jvdata_bridge_mcp.tools.horse.performance import _SQL as HORSE_PERFORMANCE_SQL
from jvdata_bridge_mcp.tools.jockey.recent_form import _SQL as JOCKEY_RECENT_FORM_SQL
from jvdata_bridge_mcp.tools.misc.composite import _SQL as COMPOSITE_SQL
from jvdata_bridge_mcp.tools.pace.beneficiaries import _SQL as BENEFICIARIES_SQL
from jvdata_bridge_mcp.tools.prediction._indicator_sql import indicator_feature_ctes
from jvdata_bridge_mcp.tools.prediction.probabilities import _SQL as PREDICTION_PROBABILITIES_SQL
from jvdata_bridge_mcp.tools.prediction.scenarios import _SQL as PREDICTION_SCENARIOS_SQL
from jvdata_bridge_mcp.tools.trainer.compare import _SQL as TRAINER_COMPARE_SQL


def test_horse_performance_aggregates_only_final_result_rows() -> None:
    """通算集計は確定結果の同一母集団からすべての指標を計算する。"""

    assert "h.is_final_result IS TRUE" in HORSE_PERFORMANCE_SQL
    assert "h.is_complete_result IS TRUE AND h.is_starter IS TRUE" in HORSE_PERFORMANCE_SQL
    assert "COUNT(*) AS starts" in HORSE_PERFORMANCE_SQL
    assert "COUNT(*) FILTER (WHERE h.finish_position = 1) AS wins" in HORSE_PERFORMANCE_SQL
    assert "COUNT(*) FILTER (WHERE h.finish_position <= 3) AS top3_finishes" in HORSE_PERFORMANCE_SQL


def test_jockey_recent_form_limits_entries_before_detail_lookups() -> None:
    """直近30件を先に確定し、巨大な結果ビューとの結合を避ける。"""

    assert "recent AS MATERIALIZED" in JOCKEY_RECENT_FORM_SQL
    assert JOCKEY_RECENT_FORM_SQL.index("LIMIT :limit") < JOCKEY_RECENT_FORM_SQL.index("LEFT JOIN LATERAL")
    assert JOCKEY_RECENT_FORM_SQL.count("LEFT JOIN LATERAL") == 2


def test_pace_beneficiaries_fetches_horse_history_per_target_horse() -> None:
    """対象馬を起点にして、巨大な過去走viewの全結合を防ぐ。"""

    assert "LEFT JOIN LATERAL" in BENEFICIARIES_SQL
    assert "WHERE h.horse_id = t.horse_id" in BENEFICIARIES_SQL


def test_track_bias_aggregates_each_similar_race_before_the_final_grouping() -> None:
    """同条件の全出走表を一括結合せず、対象レース単位で索引参照する。"""

    assert "similar_races AS MATERIALIZED" in TRACK_BIAS_SQL
    assert "per_race AS MATERIALIZED" in TRACK_BIAS_SQL
    assert "CROSS JOIN LATERAL" in TRACK_BIAS_SQL
    assert "WHERE c.race_id = r.race_id" in TRACK_BIAS_SQL
    assert "rr.is_complete_result IS TRUE AND rr.is_starter IS TRUE" in TRACK_BIAS_SQL


def test_bloodline_history_stops_before_the_target_race() -> None:
    """血統系の履歴集計へ対象レースの確定結果を混ぜない。"""

    assert "h.race_id <> :race_code" in BLOODLINE_DISTANCE_BIAS_SQL
    assert "h.is_complete_result IS TRUE AND h.is_starter IS TRUE" in BLOODLINE_DISTANCE_BIAS_SQL
    assert "hr.race_id <> :race_code" in BLOODLINE_FORM_PROFILE_SQL


def test_course_advantage_honors_explicit_or_target_race_cutoff() -> None:
    """コース成績は指定された基準日または基準レース日より前だけを集計する。"""

    assert "r.race_id <> :race_code" in COURSE_ADVANTAGE_SQL
    assert "boundary_previous.scheduled_start_at < CAST(:boundary_local AS timestamp)" in COURSE_ADVANTAGE_SQL
    assert "as_of_date" not in COURSE_ADVANTAGE_SQL


def test_trainer_comparison_stops_before_the_target_race() -> None:
    """対象レースに出走する調教師の比較成績は開催日前までに限定する。"""

    assert "e.race_id <> :race_code" in TRAINER_COMPARE_SQL


def test_prediction_probability_and_scenario_tools_fetch_history_per_target_horse() -> None:
    """全出走履歴との巨大結合を避け、対象馬ごとの索引参照で特徴量を作る。"""

    assert "target_cards AS MATERIALIZED" in PREDICTION_PROBABILITIES_SQL
    assert "target_jockeys AS MATERIALIZED" in PREDICTION_PROBABILITIES_SQL
    assert "target_trainers AS MATERIALIZED" in PREDICTION_PROBABILITIES_SQL
    assert "recency_rank <= 30" in PREDICTION_PROBABILITIES_SQL
    assert "recency_rank <= 50" in PREDICTION_PROBABILITIES_SQL
    assert "CROSS JOIN LATERAL" in PREDICTION_PROBABILITIES_SQL
    assert "WHERE h.horse_id = c.horse_id" in PREDICTION_PROBABILITIES_SQL
    assert "horse_tendencies AS MATERIALIZED" in PREDICTION_SCENARIOS_SQL
    assert "LEFT JOIN LATERAL" in PREDICTION_SCENARIOS_SQL
    assert "LIMIT 5" in PREDICTION_SCENARIOS_SQL
    assert "COALESCE(speed.speed_baseline_level, 'unavailable')" in indicator_feature_ctes("r.race_date")


def test_speed_quality_reads_the_point_in_time_model_row_without_rebuilding_baselines() -> None:
    """速度は対象馬の事前計算行を読み、全期間の時計基準を予想時に再集計しない。"""

    statement = indicator_feature_ctes("r.race_date")
    assert "JOIN jv_mart.horse_speed_quality AS stored" in statement
    assert "stored.race_id = :race_code" in statement
    assert "stored.horse_id = cards.horse_id" in statement
    assert "stored.race_date = cards.race_date" in statement
    assert "stored.history_cutoff_date" not in statement
    assert "stored.model_version = 'temporal-linear-v3-speed-2026-holdout'" in statement
    assert "stored.speed_quality_raw * recency.weight_total + 1.0" in statement
    assert "speed_global_base" not in statement
    assert "speed_daily" not in statement


def test_composite_analysis_aggregates_horse_and_partnership_history_per_entry() -> None:
    """総合スコア用の履歴を対象出走馬ごとに集約し、結果表の全結合を避ける。"""

    assert "target_cards AS MATERIALIZED" in COMPOSITE_SQL
    assert "target_jockeys AS MATERIALIZED" in COMPOSITE_SQL
    assert "target_trainers AS MATERIALIZED" in COMPOSITE_SQL
    assert COMPOSITE_SQL.count("CROSS JOIN LATERAL") == 3
    assert "WHERE h.horse_id = c.horse_id" in COMPOSITE_SQL
    assert "e.horse_id = c.horse_id" in COMPOSITE_SQL
    assert "INTERVAL '730 days'" in COMPOSITE_SQL
    assert "INTERVAL '1095 days'" in COMPOSITE_SQL
