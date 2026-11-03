"""架空データで、拡張特徴量の境界・母集団・契約とJSON一致を検証する。"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import pytest

from jvdata_bridge_mcp.ml.expanded_features import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    add_field_extensions,
    clock_context,
    official_class,
    runner_extensions,
    training_z,
)
from jvdata_bridge_mcp.ml.features import FEATURE_NAMES as BASE_NAMES


def synthetic_model() -> dict:
    from jvdata_bridge_mcp.ml.model import MODEL_FORMAT

    size = len(FEATURE_NAMES)
    return {
        "format": MODEL_FORMAT,
        "feature_version": FEATURE_VERSION,
        "features": list(FEATURE_NAMES),
        "market_included": False,
        "model_version": "synthetic_v2",
        "impute": [0.0] * size,
        "temperature": 1.0,
        "stage_powers": [1.0, 1.0],
        "calibration_status": "synthetic_only",
        "estimator": {
            "kind": "linear",
            "weights": [0.0] * size,
            "intercept": 0.0,
            "center": [0.0] * size,
            "scale": [1.0] * size,
        },
    }


def test_official_condition_uses_the_horses_age_and_keeps_unknown_explicit() -> None:
    assert official_class({"3": "005", "5+": "010"}, 3) == 30
    assert official_class({"3": "005", "5+": "010"}, 6) == 40
    assert math.isnan(official_class({"3": None, "youngest": "005"}, 3))
    assert math.isnan(official_class({"3": "005"}, None))


def test_clock_reference_excludes_target_day_and_future_and_uses_matching_ground() -> None:
    rows = [
        {
            "race_date": "2039-12-01",
            "ground": "1",
            "winner_seconds": 100.0,
            "winner_closing": 35.0,
            "pace_seconds": 36.0,
        }
        for _ in range(25)
    ]
    rows += [
        {
            "race_date": "2039-12-02",
            "ground": "2",
            "winner_seconds": 120.0,
            "winner_closing": 40.0,
            "pace_seconds": 39.0,
        }
        for _ in range(25)
    ]
    rows += [
        {"race_date": "2040-01-01", "ground": "1", "winner_seconds": 1.0},
        {"race_date": "2040-02-01", "ground": "1", "winner_seconds": 1.0},
    ]
    same = clock_context(rows, "2040-01-01", "1")
    unknown = clock_context(rows, "2040-01-01", None)
    assert same["clock_mean"] == 100 and same["clock_count"] == 25
    assert unknown["clock_mean"] == 110 and unknown["clock_count"] == 50


def test_training_reference_does_not_treat_small_samples_as_a_known_benchmark() -> None:
    assert math.isnan(training_z(50, 29, 29 * 52, 29 * 52**2))
    assert training_z(50, 30, 30 * 52, 30 * 52**2) == 10


def test_extended_features_reject_same_day_observations_and_ignore_target_outcomes() -> None:
    card = {"race_date": "2040-01-01", "track_code": "11", "official_class_rank": 30}
    before = runner_extensions(card, [], [])
    after = runner_extensions({**card, "finish_position": 1, "elapsed_seconds": 1, "final_win_odds": 1.1}, [], [])
    assert all(
        (math.isnan(before[name]) and math.isnan(after[name])) or before[name] == after[name] for name in before
    )
    with pytest.raises(ValueError, match="precede"):
        runner_extensions(card, [{"race_date": "2040-01-01"}], [])
    with pytest.raises(ValueError, match="precede"):
        runner_extensions(card, [], [{"training_date": "2040-01-01"}])


def test_field_features_use_other_runners_early_tendencies() -> None:
    rows = [
        dict(
            early_lead_rate=0.8,
            early_top3_rate=0.9,
            early_frontness=0.8,
            high_pace_quality=0.7,
            slow_pace_quality=0.4,
            clock_residual_mean=1.0,
        ),
        dict(
            early_lead_rate=0.1,
            early_top3_rate=0.3,
            early_frontness=0.2,
            high_pace_quality=0.4,
            slow_pace_quality=0.6,
            clock_residual_mean=-1.0,
        ),
    ]
    add_field_extensions(rows)
    assert rows[0]["field_lead_pressure"] == pytest.approx(0.1)
    assert rows[1]["field_lead_pressure"] == pytest.approx(0.8)
    assert rows[0]["relative_clock_ability"] == 2


def test_training_population_excludes_the_session_day(monkeypatch: pytest.MonkeyPatch) -> None:
    pd = pytest.importorskip("pandas")
    from jvdata_bridge_mcp.ml.research.expanded_dataset import normalized_training

    reference = pd.DataFrame(
        [
            dict(
                training_date="2039-12-01",
                training_type="HC",
                training_center_code="A",
                course_code="",
                n4=30,
                sum4=1560,
                sq4=81120,
                n1=30,
                sum1=390,
                sq1=5070,
            ),
            dict(
                training_date="2040-01-01",
                training_type="HC",
                training_center_code="A",
                course_code="",
                n4=1000,
                sum4=70000,
                sq4=4900000,
                n1=1000,
                sum1=20000,
                sq1=400000,
            ),
        ]
    )
    sessions = pd.DataFrame(
        [
            dict(
                training_date="2040-01-01",
                training_type="HC",
                training_center_code="A",
                course_code="",
                horse_id="synthetic",
                four_furlongs_seconds=52,
                lap_200_0_seconds=13,
                quality_status="valid",
            )
        ]
    )
    monkeypatch.setattr(
        pd,
        "read_parquet",
        lambda path: reference.copy() if path.name == "training-reference.parquet" else sessions.copy(),
    )
    result = normalized_training(Path("synthetic"))
    assert result.normalized_4f.iloc[0] == pytest.approx(0)
    assert result.normalized_last1f.iloc[0] == pytest.approx(0)
    from jvdata_bridge_mcp.ml.expanded_live import normalize_sessions

    live = normalize_sessions(sessions.to_dict("records"), reference.to_dict("records"))
    assert live[0]["normalized_4f"] == pytest.approx(result.normalized_4f.iloc[0])
    assert live[0]["normalized_last1f"] == pytest.approx(result.normalized_last1f.iloc[0])


@pytest.mark.parametrize("family", ["linear", "lightgbm", "catboost"])
def test_extended_training_and_portable_inference_agree_for_new_feature_indices(family: str) -> None:
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    pytest.importorskip("scipy")
    if family != "linear":
        pytest.importorskip(family)
    from jvdata_bridge_mcp.ml.inference import predict_probabilities, validate_model
    from jvdata_bridge_mcp.ml.research.fitting import Trial, check_export, fit_trial

    rows = []
    for race in range(30):
        for horse in range(4):
            row = {name: 0.0 for name in FEATURE_NAMES}
            winner = horse == race % 4
            row.update(
                race_id=str(race),
                race_date="2040-01-01" if race < 20 else "2040-02-01",
                win=float(winner),
                finish=1 if winner else 2 + horse,
                venue=1 + horse % 2,
                history_count=horse,
                official_class_rank=40 if winner else 30,
                clock_residual_mean=1 if winner else -1,
                speed_quality_raw=np.nan if horse % 2 else 0.5,
            )
            rows.append(row)
    frame = pd.DataFrame(rows)
    trial = Trial(
        "synthetic_v2",
        family,
        missing="median" if family == "linear" else "native",
        categorical=family != "linear",
        leaves_or_depth=3 if family == "catboost" else 7,
        min_leaf=3,
        regularization=1,
        iterations=20,
    )
    model, payload, _ = fit_trial(
        frame.iloc[:80],
        frame.iloc[80:].reset_index(drop=True),
        trial,
        iterations=20,
        feature_names=FEATURE_NAMES,
        feature_version=FEATURE_VERSION,
    )
    validate_model(payload)
    assert len(payload["impute"]) > len(BASE_NAMES)
    assert check_export(model, payload, frame.iloc[80:].reset_index(drop=True)) < 1e-8
    p = predict_probabilities(payload, frame.iloc[:4].to_dict("records"))
    assert sum(p) == pytest.approx(1)
    assert p[0] == max(p)


def test_reliability_calibration_preserves_mass_and_matches_runtime() -> None:
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    from jvdata_bridge_mcp.ml.inference import effective_temperature
    from jvdata_bridge_mcp.ml.research.expanded_calibration import apply

    frame = pd.DataFrame({"race_id": ["a"] * 3 + ["b"] * 4, "history_count": [0, 1, 2, 10, 10, 10, 10]})
    scores = np.asarray([2, 1, 0, 3, 2, 1, 0], dtype=float)
    cal = {"temperature": 1.1, "slope": 0.1, "reliability_slope": 0.2}
    result = apply(scores, frame, cal)
    from jvdata_bridge_mcp.ml.distribution import softmax

    payload = {"temperature": 1.1, "field_temperature_slope": 0.1, "reliability_temperature_slope": 0.2}
    for indices in ([0, 1, 2], [3, 4, 5, 6]):
        rows = frame.iloc[indices].to_dict("records")
        expected = softmax(scores[indices].tolist(), effective_temperature(payload, len(rows), rows))
        assert result[indices] == pytest.approx(expected)


def test_extended_selection_does_not_trade_worse_brier_for_better_log_loss() -> None:
    pytest.importorskip("pandas")
    from jvdata_bridge_mcp.ml.research.train import choose

    baseline = {
        "trial": {"name": "linear"},
        "calibrations": {"identity": {"log_loss": 1.2, "brier": 0.1}},
        "json_bytes": 10,
        "fit_seconds": 1,
    }
    bad = {
        "trial": {"name": "bad"},
        "calibrations": {"identity": {"log_loss": 1.0, "brier": 0.11}},
        "json_bytes": 100,
        "fit_seconds": 1,
    }
    assert choose([baseline, bad], baseline)["record"] == baseline


def test_live_expansion_queries_have_explicit_prior_date_and_no_hash_precedence() -> None:
    from jvdata_bridge_mcp.ml.expanded_live import CLASS_FOR_RACES_SQL, CLOCK_REFERENCE_SQL, TRAINING_POPULATION_SQL

    assert "COUNT(DISTINCT NULLIF" in CLASS_FOR_RACES_SQL
    assert "artifact_hash" not in CLASS_FOR_RACES_SQL
    assert "r.race_id=ANY" in CLASS_FOR_RACES_SQL
    assert "p.race_date<q.race_date" in CLOCK_REFERENCE_SQL
    assert "r.race_date<CAST(:cutoff AS date)" in CLOCK_REFERENCE_SQL
    assert "training_date<CAST(:cutoff AS date)" in TRAINING_POPULATION_SQL


def test_training_windows_are_crossed_with_full_features_before_window_is_selected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pd = pytest.importorskip("pandas")
    from dataclasses import asdict

    from jvdata_bridge_mcp.ml.research import train as expansion_study

    seen = []

    def fake_trial(
        frame: object, output: Path, year: int, trial: object, start: int, disabled: tuple, protocol_hash: str
    ) -> dict:
        fields = asdict(trial)
        name = fields["name"]
        seen.append((name, start, disabled))
        loss = 1.8 if name == "all_2012" else 1.9 if name.startswith("base_") else 2.0
        return {
            "trial": fields,
            "training_start": start,
            "disabled": list(disabled),
            "calibrations": {"identity": {"log_loss": loss, "brier": 0.08}},
            "json_bytes": 100,
            "fit_seconds": 1,
        }

    monkeypatch.setattr(expansion_study, "trial_selection", fake_trial)
    choice, control, _, _ = expansion_study.selected_pipeline(pd.DataFrame(), tmp_path, 2040, "synthetic")
    assert choice["record"]["trial"]["name"] == "all_2012"
    assert control["record"]["trial"]["name"] == "base_2012"
    assert all(any(name == f"all_{start}" for name, _, _ in seen) for start in (2012, 2015, 2018))
    assert all(start == 2012 for name, start, _ in seen if name.startswith("without_"))


def test_extended_contract_integrates_into_existing_tools_without_a_model_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jvdata_bridge_mcp.ml import live, prediction
    from jvdata_bridge_mcp.tools._race_boundary import RaceBoundary
    from jvdata_bridge_mcp.tools.prediction import features, probabilities

    model = synthetic_model()
    cards = [
        {
            "race_id": "2040061005010111",
            "race_date": "2040-06-10",
            "horse_number": i,
            "horse_id": f"synthetic{i}",
            "horse_name": f"S{i}",
        }
        for i in range(1, 4)
    ]
    values = [{name: 0.0 for name in FEATURE_NAMES} for _ in cards]
    boundary = RaceBoundary(
        race_code="2040061005010111",
        race_date="2040-06-10",
        analysis_at="2040-06-10T10:00:00+09:00",
        history_before_at="2040-06-10T10:00:00+09:00",
    )

    async def evidence(*args: object, **kwargs: object) -> tuple:
        assert kwargs["extended"] is True
        return (
            cards,
            values,
            boundary,
            {
                "summaries": {
                    c["horse_id"]: dict(
                        starts=0,
                        wins=0,
                        top3=0,
                        average_finish_position=math.nan,
                        average_last_three_furlongs=math.nan,
                    )
                    for c in cards
                }
            },
        )

    monkeypatch.setattr(live, "live_features", evidence)
    monkeypatch.setattr(prediction, "live_features", evidence)
    monkeypatch.setattr(probabilities, "load_active_model", lambda: model)
    monkeypatch.setattr(features, "load_active_model", lambda: model)
    result = asyncio.run(probabilities.build_predict_race_probabilities_tool(object())("2040061005010111"))
    assert sum(p.win_probability for p in result.probabilities) == pytest.approx(1)
    assert all(p.top3_probability == pytest.approx(1) for p in result.probabilities)
    matrix = asyncio.run(features.build_get_horse_feature_matrix_tool(object())("2040061005010111"))
    assert set(matrix.ability_features[0]) == {"horse_number", *FEATURE_NAMES}
    assert "NaN" not in matrix.model_dump_json()


def test_final_evaluation_checks_expanded_sources_before_reading_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("pandas")
    from datetime import datetime

    from jvdata_bridge_mcp.ml.research import future
    from jvdata_bridge_mcp.tools._source_timing import JST

    monkeypatch.setattr(future, "archive_directory", lambda path: path)
    monkeypatch.setattr(future, "now_jst", lambda: datetime(2040, 3, 1, tzinfo=JST))
    monkeypatch.setattr(
        future, "digest_file", lambda path: "plan" if path.name == "execution-plan.json" else "changed"
    )
    (tmp_path / "adoption-rule.json").write_text(json.dumps({"final_end_exclusive": "2040-02-01"}), encoding="utf-8")
    (tmp_path / "frozen-optimized-candidate.json").write_text(json.dumps(synthetic_model()), encoding="utf-8")
    (tmp_path / "optimization.json").write_text(json.dumps({"execution_plan_sha256": "plan"}), encoding="utf-8")
    (tmp_path / "execution-plan.json").write_text(
        json.dumps({"source_sha256": {"expanded_features.py": "original"}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="changed after freezing"):
        future.evaluate(tmp_path, tmp_path / "unread-features.parquet", tmp_path / "unread-baseline.parquet")
