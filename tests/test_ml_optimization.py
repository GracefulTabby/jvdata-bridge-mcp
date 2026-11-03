"""架空データで、時系列分割・較正・候補選択・日単位区間を検証する。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("scipy")


def test_nested_fold_has_no_overlap_and_preserves_a_separate_final_calibration() -> None:
    from jvdata_bridge_mcp.ml.research.common import fold_ranges, validate_partitions

    ranges = fold_ranges(2040)
    parts = {name: pd.DataFrame({"race_id": [name], "race_date": [interval[0]]}) for name, interval in ranges.items()}
    validate_partitions(parts)
    assert ranges["selection"][1] <= ranges["final_calibration"][0]
    assert ranges["final_calibration"][1] <= ranges["assessment"][0]
    parts["assessment"]["race_id"] = "selection"
    with pytest.raises(ValueError, match="overlap"):
        validate_partitions(parts)


def test_model_selection_rejects_log_loss_gain_with_worse_brier() -> None:
    from jvdata_bridge_mcp.ml.research.train import choose

    records = [
        {
            "trial": {"name": name},
            "calibrations": {"identity": {"log_loss": loss, "brier": brier}},
            "json_bytes": size,
            "fit_seconds": 1,
        }
        for name, loss, brier, size in [
            ("linear", 1.2, 0.15, 10),
            ("overconfident", 1.0, 0.16, 100),
            ("balanced", 1.1, 0.14, 90),
        ]
    ]
    assert choose(records, records[0])["record"]["trial"]["name"] == "balanced"


def test_calibration_preserves_race_mass_for_different_field_sizes() -> None:
    from jvdata_bridge_mcp.ml.research.calibration import calibrated_probabilities, fit_calibration, winner_scores

    frame = pd.DataFrame({"race_id": ["a"] * 3 + ["b"] * 5, "win": [1, 0, 0, 0, 1, 0, 0, 0]})
    scores = np.asarray([3, 1, 0, 0, 3, 2, 1, -1], dtype=float)
    for kind in ("identity", "temperature", "field_temperature"):
        calibration = fit_calibration(scores, frame, kind)
        p = calibrated_probabilities(scores, frame, calibration)
        assert p[:3].sum() == pytest.approx(1)
        assert p[3:].sum() == pytest.approx(1)
        assert np.isfinite(p).all()
        assert winner_scores(frame, p)["log_loss"] >= 0


def test_day_bootstrap_does_not_treat_identical_same_day_races_as_independent() -> None:
    from jvdata_bridge_mcp.ml.research.metrics import paired_day_interval

    rows = [{"race_id": str(i), "log_loss": 1 + (0.2 if i < 50 else -0.2)} for i in range(100)]
    baseline = {"per_race": [{"race_id": str(i), "log_loss": 1} for i in range(100)]}
    dates = {str(i): "2040-01-01" if i < 50 else "2040-01-02" for i in range(100)}
    result = paired_day_interval({"per_race": rows}, baseline, "log_loss", dates)
    assert result["days"] == 2 and result["races"] == 100
    assert result["mean_delta"] == pytest.approx(0)
    assert result["interval_95"] == pytest.approx([-0.2, 0.2])


def test_external_comparisons_exclude_partial_rosters_and_reject_duplicates() -> None:
    from jvdata_bridge_mcp.ml.research.evaluation import common_rosters

    frame = pd.DataFrame({"race_id": ["full"] * 3 + ["partial"] * 3, "horse_id": list("abcdef")})
    external = frame.iloc[:5].copy()
    assert common_rosters(frame, external, "horse_id") == {"full"}
    with pytest.raises(ValueError, match="duplicate"):
        common_rosters(frame, pd.concat([external, external.iloc[:1]]), "horse_id")


def test_missingness_condition_evaluates_whole_races() -> None:
    from jvdata_bridge_mcp.ml.research.evaluation import conditions
    from jvdata_bridge_mcp.ml.research.metrics import probability_metrics

    scores = pd.DataFrame(
        {
            "race_id": ["a"] * 3 + ["b"] * 3,
            "race_date": ["2040-01-01"] * 3 + ["2040-02-01"] * 3,
            "horse_id": list("abcdef"),
            "win": [1, 0, 0, 0, 1, 0],
            "finish": [1, 2, 3, 2, 1, 3],
            "venue_code": ["01"] * 6,
            "track_code": ["11"] * 6,
            "field_size": [3] * 6,
            "refreshed_fit": [0.5, 0.3, 0.2, 0.2, 0.6, 0.2],
        }
    )
    features = scores[["race_id", "horse_id"]].assign(
        distance=1600, grade=2, history_count=[1, 5, 5, 6, 5, 8], speed_missing=[1, 0, 0, 0, 0, 0]
    )
    metrics = probability_metrics(scores, scores.refreshed_fit.to_numpy(), joint=False)
    result = conditions(scores, features, metrics)
    missing = result["any_speed_missing=1"]
    assert missing["races"] == 1
    assert sum(row["count"] for row in missing["win_calibration"]) == 3
    assert missing["log_loss"] == pytest.approx(-np.log(0.5))


def test_recipe_opponents_exclude_withdrawals_even_without_their_result_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jvdata_bridge_mcp.ml.research.history import enriched_runs

    rows = []
    for race, race_date in (("past", "2039-12-01"), ("target", "2040-01-01")):
        for horse in range(4):
            active = horse < 3
            rows.append(
                {
                    "race_id": race,
                    "race_date": race_date,
                    "horse_id": str(horse),
                    "horse_number": horse + 1,
                    "venue_code": "01",
                    "track_code": "11",
                    "distance_meters": 1600,
                    "starters": 3,
                    "finish_position": horse + 1 if active else None,
                    "time_difference_seconds": 0.2 * horse if active else None,
                    "last_three_furlongs_seconds": 34 + horse if active else None,
                    **{f"corner_{corner}_position": horse + 1 if active else None for corner in range(1, 5)},
                    "is_final_result": True if active else None,
                    "is_complete_result": True if active else None,
                    "is_starter": active,
                    "is_finisher": active,
                }
            )
    monkeypatch.setattr(pd, "read_parquet", lambda *args, **kwargs: pd.DataFrame(rows))
    result = enriched_runs(Path("unused-synthetic.parquet"))
    first = result[(result.race_id == "target") & (result.horse_id == "0")].iloc[0]
    assert first.opponent_strength == pytest.approx((0.5 + 0.3) / 2)


def test_final_evaluation_cannot_read_outcomes_before_the_fixed_period_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jvdata_bridge_mcp.ml.research import future
    from jvdata_bridge_mcp.tools._observation_scope import JST

    monkeypatch.setattr(future, "archive_directory", lambda path: path)
    monkeypatch.setattr(future, "now_jst", lambda: datetime(2040, 1, 10, tzinfo=JST))
    (tmp_path / "adoption-rule.json").write_text(json.dumps({"final_end_exclusive": "2040-02-01"}), encoding="utf-8")
    result = future.evaluate(tmp_path, tmp_path / "unread-outcomes.parquet", tmp_path / "unread-baseline.parquet")
    assert result["status"] == "pending_fixed_future_period" and result["labels_inspected"] is False


def test_study_freezes_declared_dates_sources_and_adoption_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jvdata_bridge_mcp.ml._development import digest_file
    from jvdata_bridge_mcp.ml.research import future, train
    from jvdata_bridge_mcp.tools._observation_scope import JST

    monkeypatch.setattr(train, "archive_directory", lambda path: path)
    monkeypatch.setattr(train, "now_jst", lambda: datetime(2040, 11, 3, tzinfo=JST))
    (tmp_path / "data").mkdir()
    frame = pd.DataFrame(
        {
            "race_id": ["synthetic"] * 3,
            "horse_id": list("abc"),
            "race_date": ["2040-08-01"] * 3,
            "history_max_date": [None] * 3,
        }
    )
    frame.to_parquet(tmp_path / "data/features.parquet", index=False)
    with pytest.raises(ValueError, match="future"):
        train.freeze(tmp_path, date(2040, 10, 1), date(2040, 11, 3), date(2040, 12, 1))
    train.freeze(tmp_path, date(2040, 10, 1), date(2040, 11, 4), date(2040, 12, 1))
    assert len(train.load(tmp_path)[0]) == 3
    protocol = json.loads((tmp_path / "protocol.json").read_text(encoding="utf-8"))
    plan = json.loads((tmp_path / "execution-plan.json").read_text(encoding="utf-8"))
    assert protocol["outer_development_years"] == [2037, 2038, 2039, 2040]
    assert protocol["candidate_calibration_start"] == "2040-07-01"
    assert {"ml/research/train.py", "tools/prediction/_indicator_sql.py"} <= plan["source_sha256"].keys()
    with pytest.raises(ValueError, match="already frozen"):
        train.freeze(tmp_path, date(2040, 10, 1), date(2040, 11, 4), date(2040, 12, 1))
    (tmp_path / "optimization.json").write_text(
        json.dumps(
            {
                "execution_plan_sha256": digest_file(tmp_path / "execution-plan.json"),
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "frozen-optimized-candidate.json").write_text("{}", encoding="utf-8")
    rules_path = tmp_path / "adoption-rule.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    rules["minimum_common_races"] = 1
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    monkeypatch.setattr(future, "archive_directory", lambda path: path)
    monkeypatch.setattr(future, "now_jst", lambda: datetime(2040, 12, 2, tzinfo=JST))
    monkeypatch.setattr(future, "validate_model", lambda model: None)
    with pytest.raises(ValueError, match="adoption rule changed"):
        train.load(tmp_path)
    with pytest.raises(ValueError, match="adoption rule changed"):
        future.evaluate(tmp_path, tmp_path / "unread-outcomes.parquet", tmp_path / "unread-baseline.parquet")


def test_failed_export_validation_resumes_native_fit_but_rejects_changed_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jvdata_bridge_mcp.ml.research import train
    from jvdata_bridge_mcp.ml.research.fitting import Trial

    rows = []
    for index, race_date in enumerate(("2018-01-02", "2039-01-02", "2039-04-02", "2039-07-02")):
        rows.extend(
            {"race_id": str(index), "race_date": race_date, "win": int(horse == 0), "history_count": 3}
            for horse in range(3)
        )
    frame = pd.DataFrame(rows)
    counts = {"fits": 0, "checks": 0}

    def fit(*args: object, **kwargs: object) -> tuple[dict[str, object], dict[str, object], int]:
        counts["fits"] += 1
        return {}, {"estimator": {"kind": "linear"}}, 0

    def check(*args: object) -> float:
        counts["checks"] += 1
        if counts["checks"] == 1:
            raise ValueError("synthetic parity failure")
        return 0.0

    monkeypatch.setattr(train, "archive_directory", lambda path: path.mkdir(parents=True, exist_ok=True) or path)
    monkeypatch.setattr(train, "fit_trial", fit)
    monkeypatch.setattr(train, "check_export", check)
    monkeypatch.setattr(train, "native_scores", lambda model, payload, part: np.zeros(len(part)))
    trial = Trial("linear", "linear")
    with pytest.raises(ValueError, match="parity failure"):
        train.trial_selection(frame, tmp_path, 2040, trial, 2018, (), "protocol")
    assert (tmp_path / "fold-2040/linear/fit.json").exists()
    with pytest.raises(ValueError, match="identity changed"):
        train.trial_selection(frame, tmp_path, 2040, trial, 2018, (), "changed-protocol")
    record = train.trial_selection(frame, tmp_path, 2040, trial, 2018, (), "protocol")
    assert counts == {"fits": 1, "checks": 2}
    assert record["native_export_max_error"] == 0


@pytest.mark.parametrize("family", ["lightgbm", "catboost"])
def test_trained_categorical_model_matches_portable_json_with_unseen_categories_and_missing_values(
    family: str,
) -> None:
    pytest.importorskip(family)
    from jvdata_bridge_mcp.ml.features import FEATURE_NAMES
    from jvdata_bridge_mcp.ml.research.fitting import Trial, check_export, fit_trial

    rows = []
    for race in range(30):
        for horse in range(4):
            row = {name: 0.0 for name in FEATURE_NAMES}
            winner = horse == race % 4
            row.update(
                {
                    "race_id": str(race),
                    "race_date": "2040-01-01" if race < 20 else "2040-02-01",
                    "win": float(winner),
                    "finish": 1 if winner else 2 + horse,
                    "venue": 1 if winner else 2 + horse % 2,
                    "grade": 1,
                    "dirt": 0,
                    "speed_quality_raw": np.nan if horse % 2 else horse / 4,
                }
            )
            rows.append(row)
    frame = pd.DataFrame(rows)
    trial = Trial(
        "synthetic",
        family,
        missing="native",
        categorical=True,
        leaves_or_depth=3 if family == "catboost" else 7,
        min_leaf=3,
        regularization=1,
        learning_rate=0.15,
        iterations=25,
    )
    model, payload, _ = fit_trial(
        frame.iloc[:80].reset_index(drop=True), frame.iloc[80:].reset_index(drop=True), trial, iterations=25
    )
    assert check_export(model, payload, frame.iloc[80:].reset_index(drop=True)) < 1e-8
