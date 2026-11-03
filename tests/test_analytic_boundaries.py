"""予測・分析ツールの事前時点と対象出走馬境界の回帰試験。"""

from __future__ import annotations

import asyncio

import pytest

from jvdata_bridge_mcp.tools.bloodline.distance_bias import _TARGET_SQL as BLOODLINE_DISTANCE_SQL
from jvdata_bridge_mcp.tools.bloodline.scoreboard import _SQL as BLOODLINE_SCOREBOARD_SQL
from jvdata_bridge_mcp.tools.misc.composite import _FEATURE_CTES as COMPOSITE_FEATURE_CTES
from jvdata_bridge_mcp.tools.prediction import scenarios
from jvdata_bridge_mcp.tools.prediction.features import _FEATURE_CTES as PREDICTION_FEATURE_CTES
from jvdata_bridge_mcp.tools.prediction.positions import (
    AlternativeLeaderScenario,
    ConditionalEarlyPosition,
    FrontPairScenario,
    HorsePositionScenario,
    PositionScenarioUnavailable,
)
from jvdata_bridge_mcp.tools.prediction.probabilities import _FEATURE_CTES as PROBABILITY_FEATURE_CTES
from jvdata_bridge_mcp.tools.prediction.scenarios import _FEATURE_CTES as SCENARIO_FEATURE_CTES
from jvdata_bridge_mcp.tools.prediction.scenarios import INDICATOR_MODEL_VERSION
from jvdata_bridge_mcp.tools.race.condition_changes import (
    RaceLastStart,
    _build_condition_changes,
    _class_change_payload,
    _class_display,
    _class_rank,
    _course_name,
    _raw_running_time,
    _raw_time_difference,
    _track_description,
)
from jvdata_bridge_mcp.tools.trainer import distance_specialty
from jvdata_bridge_mcp.tools.training.condition_summary import _SQL as TRAINING_SUMMARY_SQL
from jvdata_bridge_mcp.tools.training.evaluation import _SQL as TRAINING_EVALUATION_SQL
from jvdata_bridge_mcp.tools.training.intensity import _SQL as TRAINING_INTENSITY_SQL


def _target_cards_clause(statement: str) -> str:
    """共有特徴量SQLから、対象カード集合だけの句を取り出す。"""

    start = statement.index("target_cards AS MATERIALIZED")
    end = statement.index("), speed_features AS MATERIALIZED", start)
    return statement[start:end]


def test_prediction_features_preserve_declared_entries_when_cancellation_timing_unknown() -> None:
    """共有指標は最新取消を過去時点へ遡及せず出馬表の全馬を残す。"""

    for statement in (
        PREDICTION_FEATURE_CTES,
        PROBABILITY_FEATURE_CTES,
        SCENARIO_FEATURE_CTES,
        COMPOSITE_FEATURE_CTES,
    ):
        target_clause = _target_cards_clause(statement)
        assert "c.abnormality_code" not in target_clause
        assert "c.abnormality_name" not in statement
        assert "c.abnormality_name" not in statement
        assert "rr.is_starter IS FALSE" not in statement
        assert "NOT EXISTS" not in statement
        assert "race_results" not in target_clause
        assert "is_starter" not in target_clause


def test_local_scenario_output_keeps_position_probabilities_separate_from_pace_probabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """馬別位置の確率と、未校正の相対ペースを混同させない。"""

    async def fake_fetch_rows(
        _database: object,
        _sql: str,
        _parameters: dict[str, object],
        **_kwargs: object,
    ) -> list[dict[str, object]]:
        return [
            {
                "race_code": "2026082307030212",
                "history_before_date": "2026-08-23",
                "running_style_code": "front_runner",
                "running_style": "front_runner",
                "runner_count": 1,
                "horse_numbers": [1],
                "expected_front_runner_count": 0.1,
                "expected_forward_runner_count": 0.34,
                "style_coverage_rate": 0.0,
                "average_first_corner_position": None,
                "average_last_three_furlongs": None,
                "speed_quality_score": None,
                "speed_quality_coverage": None,
                "speed_known_runner_count": 0,
                "speed_baseline_level": "unavailable",
                "indicator_version": INDICATOR_MODEL_VERSION,
                "pace_pressure_index": 0.0,
                "scenario_label": "balanced_pace",
            }
        ]

    async def fake_position_scenarios(
        *_args: object,
    ) -> tuple[list[HorsePositionScenario], int, list[AlternativeLeaderScenario], list[FrontPairScenario], float]:
        horses = [
            HorsePositionScenario(
                horse_number=1,
                horse_name="サンプル",
                history_races=5,
                history_coverage=0.25,
                jockey_history_rides=50,
                early_expected_rank=2.0,
                early_lead_probability=0.4,
                early_top3_probability=0.8,
                early_position_band_probabilities={"lead": 0.4, "forward": 0.2, "midfield": 0.2, "rear": 0.2},
                fourth_expected_rank=3.0,
                fourth_lead_probability=0.2,
                fourth_top3_probability=0.6,
                fourth_position_band_probabilities={"lead": 0.2, "forward": 0.2, "midfield": 0.3, "rear": 0.3},
            )
        ]
        branches = [
            AlternativeLeaderScenario(
                leader_horse_number=1,
                leader_horse_name="サンプル",
                leader_probability=1.0,
                seed=223,
                simulations=5000,
                horses=[
                    ConditionalEarlyPosition(
                        horse_number=1,
                        horse_name="サンプル",
                        expected_rank=1.0,
                        top3_probability=1.0,
                        position_band_probabilities={"lead": 1.0, "forward": 0.0, "midfield": 0.0, "rear": 0.0},
                    )
                ],
            )
        ]
        return horses, 123, branches, [], 0.0

    monkeypatch.setattr(scenarios, "fetch_rows", fake_fetch_rows)
    monkeypatch.setattr(scenarios, "forecast_position_scenarios", fake_position_scenarios)
    tool = scenarios.build_simulate_race_scenarios_tool(object())

    result = asyncio.run(tool("2026082307030212"))

    assert result.race_code == "2026082307030212"
    assert result.history_before_date == "2026-08-23"
    assert "as_of_datetime" not in result.model_dump()
    assert result.model_version == INDICATOR_MODEL_VERSION
    assert result.output_granularity == "style_and_horse_position"
    assert result.probability_status == "horse_positions_only"
    assert result.simulation_status == "horse_positions_run"
    assert result.pace_probabilities is None
    assert result.scenarios[0].speed_quality_score is None
    assert result.scenarios[0].speed_known_runner_count == 0
    assert "scenario_confidence" not in result.model_dump()["scenarios"][0]
    assert "scenario_sources" not in result.model_dump()
    assert result.simulations == 5000
    assert result.seed == 123
    assert result.horses is not None
    assert result.horses[0].early_lead_probability == 0.4
    assert result.leader_branches is not None
    assert result.leader_branches[0].leader_horse_number == 1
    assert result.other_leaders_probability == 0.0
    assert result.lead_uncertainty_index == 0.0
    assert result.front_pair_scenarios == []


def test_scenario_keeps_pace_result_when_position_model_does_not_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    """少頭数戦などで馬別位置を出せなくても、ペース側の結果は残す。"""

    async def fake_fetch_rows(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return []

    async def unavailable(
        *_args: object,
    ) -> tuple[list[HorsePositionScenario], int, list[AlternativeLeaderScenario], list[FrontPairScenario], float]:
        raise PositionScenarioUnavailable("position model requires 8–18 active runners")

    monkeypatch.setattr(scenarios, "fetch_rows", fake_fetch_rows)
    monkeypatch.setattr(scenarios, "forecast_position_scenarios", unavailable)

    result = asyncio.run(scenarios.build_simulate_race_scenarios_tool(object())("2026082307030212"))
    assert result.scenarios == []
    assert result.horses is None
    assert result.position_model_version is None
    assert result.position_unavailable_reason == "position model requires 8–18 active runners"
    assert result.output_granularity == "style_aggregate"
    assert result.simulations is None
    assert result.simulation_status == "unsupported_race"
    assert result.leader_branches is None
    assert result.other_leaders_probability is None
    assert result.front_pair_scenarios is None


def test_bloodline_and_training_preserve_prior_evidence_for_declared_cards() -> None:
    """血統・調教は最新取消で過去事実を消さない。"""

    for statement in (
        BLOODLINE_DISTANCE_SQL,
        BLOODLINE_SCOREBOARD_SQL,
        TRAINING_EVALUATION_SQL,
        TRAINING_SUMMARY_SQL,
        TRAINING_INTENSITY_SQL,
    ):
        assert "NOT LIKE '%取消%'" not in statement
        assert "NOT LIKE '%除外%'" not in statement
        assert "rr.is_starter IS FALSE" not in statement
        assert "NOT EXISTS" not in statement
        assert "race_results" not in statement


def test_trainer_distance_specialty_maps_public_japanese_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公開入力をread modelのsurface/distance bandへ正規化する。"""

    captured: dict[str, object] = {}

    async def fake_fetch_rows(_database: object, _sql: str, parameters: dict[str, object]) -> list[dict[str, object]]:
        captured.update(parameters)
        return []

    monkeypatch.setattr(distance_specialty, "fetch_rows", fake_fetch_rows)
    tool = distance_specialty.build_analyze_trainer_distance_specialty_tool(object())
    result = asyncio.run(tool("短距離", "ダート", "01168"))

    assert result.specialties == []
    assert captured["distance_band_code"] == "dirt_1600_or_less"
    assert captured["surface_code"] == "dirt"
    assert captured["trainer_code"] == "01168"
    assert "distance_category" not in captured
    assert "track_type" not in captured

    with pytest.raises(ValueError, match="track_type must be one of"):
        asyncio.run(tool("短距離", "芝ダート", "01168"))
    with pytest.raises(ValueError, match="distance_category must be one of"):
        asyncio.run(tool("超長距離", "芝", "01168"))


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("GIII（平地競走）", 70),
        ("GII（平地競走）", 80),
        ("GI（平地競走）", 90),
        ("G1（平地競走）", 90),
        ("JPN1", 90),
        ("G2", 80),
        ("JPN2", 80),
        ("G3", 70),
        ("JPN3", 70),
        ("LISTED", 65),
        ("L", 65),
        ("オープン", 60),
        ("OP", 60),
        ("3勝クラス", 50),
        ("1600万下", 50),
        ("2勝クラス", 40),
        ("1000万下", 40),
        ("1勝クラス", 30),
        ("500万下", 30),
        ("未勝利", 10),
        ("新馬", 5),
        (None, None),
        ("条件不明", None),
    ],
)
def test_class_rank_matches_authoritative_tokens(label: str | None, expected: int | None) -> None:
    """クラス表記は定義された比較用の順序値に対応する。"""

    assert _class_rank(label) == expected


def test_class_display_normalizes_read_model_grade_labels() -> None:
    """read modelのG1/G2/G3表記をローマ数字へ正規化する。"""

    assert _class_display("G1（平地競走）") == "GI"
    assert _class_display("G2（平地競走）") == "GII"
    assert _class_display("G3（平地競走）") == "GIII"
    assert _class_display("Jpn1") == "JPN1"


def test_class_change_payload_reports_upgrade_delta_and_unknown() -> None:
    """GIIからGIを10点上昇とし、未知・欠落はunknown/nullにする。"""

    assert _class_change_payload("G1（平地競走）", "G2（平地競走）") == {
        "current": "G1（平地競走）",
        "previous": "G2（平地競走）",
        "change": "upgrade",
        "current_rank": 90,
        "previous_rank": 80,
        "rank_delta": 10,
    }
    assert _class_change_payload("条件不明", "G2") == {
        "current": "条件不明",
        "previous": "G2",
        "change": "unknown",
        "current_rank": None,
        "previous_rank": 80,
        "rank_delta": None,
    }
    assert _class_change_payload(None, None)["change"] == "unknown"


def test_condition_change_display_normalizes_track_and_time_tokens() -> None:
    """前走比較の表示値を丸めず、外回りとJRA raw時計を保持する。"""

    assert _track_description("平地 芝 右回り 外回り") == "芝・右外"
    assert _course_name("中山競馬場") == "中山"
    assert _raw_running_time(184.8) == "3048"
    assert _raw_time_difference(0.7) == "+007"


def test_condition_changes_do_not_claim_a_change_without_a_previous_start() -> None:
    """前走がない場合は「変更あり」ではなく不明とする。"""

    row: dict[str, object] = {
        "current_distance": 1600,
        "current_track": "芝・左",
        "current_racecourse_name": "中京",
        "current_track_condition": "良",
        "current_class_name": "G1",
        "current_assigned_weight": 57.0,
        "current_jockey_name": "騎手A",
        "race_date": "2026-08-23",
    }

    changes = _build_condition_changes(row, None)

    assert changes.distance.changed is None
    assert changes.surface.changed is None
    assert changes.racecourse.changed is None
    assert changes.track_condition.changed is None
    assert changes.jockey_changed is None


def test_condition_changes_set_distance_changed_from_the_numeric_delta() -> None:
    """距離のchangedは差分と同じ事実を表す。"""

    previous = RaceLastStart.model_construct(
        distance=1400,
        track="芝・左",
        racecourse_name="中京",
        class_name="G2",
        track_condition="良",
        race_date="2026-08-01",
        assigned_weight=56.0,
        jockey_name="騎手A",
    )
    row: dict[str, object] = {
        "current_distance": 1600,
        "current_track": "芝・左",
        "current_racecourse_name": "中京",
        "current_track_condition": "良",
        "current_class_name": "G1",
        "current_assigned_weight": 57.0,
        "current_jockey_name": "騎手A",
        "race_date": "2026-08-23",
    }

    changes = _build_condition_changes(row, previous)

    assert changes.distance.delta == 200
    assert changes.distance.direction == "延長"
    assert changes.distance.changed is True
    assert changes.surface.changed is False
    assert changes.race_class.change == "upgrade"
