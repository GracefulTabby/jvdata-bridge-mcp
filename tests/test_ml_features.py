"""対象結果遮断、脚質段階、欠測の学習契約。全て架空の入力。"""

from __future__ import annotations

import math

import pytest

from jvdata_bridge_mcp.ml.features import (
    FEATURE_NAMES,
    add_field_ranks,
    corner,
    enrich_run,
    recipe_updates,
    runner_features,
)
from jvdata_bridge_mcp.ml.live import HISTORY_SQL, PEOPLE_SQL, TRAINING_SQL


def _card() -> dict[str, object]:
    return {
        "race_date": "2040-06-10",
        "horse_id": "synthetic",
        "horse_number": 1,
        "horse_age": 3,
        "venue_code": "05",
        "track_code": "11",
        "distance_meters": 1600,
        "assigned_weight_kg": 55,
        "jockey_id": "j",
        "trainer_id": "t",
    }


def _run() -> dict[str, object]:
    return enrich_run(
        {
            "race_date": "2040-05-01",
            "starters": 12,
            "finish_position": 2,
            "corner_1_position": 8,
            "corner_4_position": 1,
            "distance_meters": 1600,
            "track_code": "11",
            "venue_code": "05",
            "running_style_code": "1",
            "jockey_id": "j",
            "assigned_weight_kg": 55,
            "is_final_result": True,
            "is_complete_result": True,
            "is_starter": True,
            "is_finisher": True,
        }
    )


def test_late_advance_is_not_early_lead() -> None:
    run = _run()
    assert run["early_lead"] == 0
    assert run["early_top3"] == 0
    assert run["makuri"] == 1
    assert run["rank_gain"] == 7
    assert run["fourth_frontness"] == 1
    assert math.isnan(corner({"corner_4_position": 1}, "early"))


def test_missing_speed_has_explicit_indicator_and_full_feature_schema() -> None:
    f = runner_features(_card(), [], [], [], None, [], field_size=12, pace_history=[])
    add_field_ranks([f])
    assert set(FEATURE_NAMES) <= set(f)
    assert f["speed_missing"] == 1
    assert math.isnan(f["speed_quality_raw"])
    assert math.isnan(f["speed_quality_score"])


@pytest.mark.parametrize("race_day", ["2040-06-10", "2040-06-11"])
def test_same_day_and_future_history_are_rejected(race_day: str) -> None:
    run = _run()
    run["race_date"] = race_day
    with pytest.raises(ValueError, match="strictly precede"):
        runner_features(_card(), [run], [], [], None, [], field_size=12, pace_history=[])


def test_target_result_and_historical_market_are_not_ability_features() -> None:
    a, b = _run(), _run()
    a["market_quality"], b["market_quality"] = 0.1, 0.9
    left = runner_features(_card(), [a], [], [], None, [], field_size=12, pace_history=[])
    right = runner_features(
        {**_card(), "finish_position": 1, "final_win_odds": 1.1}, [b], [], [], None, [], field_size=12, pace_history=[]
    )
    add_field_ranks([left])
    add_field_ranks([right])
    for key in FEATURE_NAMES:
        assert left[key] == right[key] or (math.isnan(left[key]) and math.isnan(right[key]))
    assert all("market" not in name and "odds" not in name for name in FEATURE_NAMES)
    assert "h.race_date<CAST(:cutoff AS date)" in HISTORY_SQL
    assert "c.race_date<CAST(:cutoff AS date)" in PEOPLE_SQL
    assert "training_date<LEAST" in TRAINING_SQL


@pytest.mark.parametrize("recipe", ["recent_fast_distance_kernel", "recent_slow_distance_kernel"])
def test_recipe_recalculation_and_runtime_share_the_same_aggregation(recipe: str) -> None:
    history = []
    for index in range(8):
        run = _run()
        run["race_date"] = f"2040-05-{20 - index:02d}"
        run["finish_quality"] = index / 8
        history.append(run)
    training = [{"training_date": "2040-05-20", "four_furlongs_seconds": 52, "lap_200_0_seconds": 12}]
    expected = recipe_updates(_card(), history, training, recipe)
    actual = runner_features(
        _card(), history, [], [], None, training, field_size=12, pace_history=[], feature_recipe=recipe
    )
    for key, value in expected.items():
        assert actual[key] == value or (math.isnan(actual[key]) and math.isnan(value))
    assert actual["recent_count"] == (3 if "fast" in recipe else 8)
