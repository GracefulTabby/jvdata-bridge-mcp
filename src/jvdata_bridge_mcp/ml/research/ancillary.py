"""ペース・位置の比較研究。能力モデルの採用判断と分離する。"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import numpy as np

from jvdata_bridge_mcp.ml._development import archive_directory, write_json
from jvdata_bridge_mcp.ml.features import day, enrich_run, mean, number, representative_pace_features, valid_run
from jvdata_bridge_mcp.ml.research.common import fold_ranges, validate_partitions
from jvdata_bridge_mcp.ml.research.metrics import calibration_curve, pace_metrics
from jvdata_bridge_mcp.tools.prediction._positions_core import (
    expanded_position_features,
    horse_long_front_strength,
    predict_hybrid_position,
)


def positional_dataset(source: Path, output: Path) -> Path:
    import pandas as pd

    output = archive_directory(output)
    path = output / "positions.parquet"
    if path.exists():
        return path
    raw = (
        pd.read_parquet(source / "runs.parquet")
        .sort_values(["race_date", "race_id", "horse_number"])
        .replace({np.nan: None})
    )
    columns = raw.columns.tolist()
    records = (dict(zip(columns, values, strict=True)) for values in raw.itertuples(index=False, name=None))
    horses: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=20))
    jockeys: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=50))
    last_runs: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=5))
    output_rows, representative_styles = [], []
    style_mismatch = {
        "runs": 0,
        "representative_front_but_not_early_lead": 0,
        "early_lead_but_other_style": 0,
        "late_advance_to_front": 0,
        "only_fourth_recorded": 0,
    }
    for race_day, daily_iter in itertools.groupby(records, key=lambda r: r["race_date"]):
        pending = []
        for _, race_iter in itertools.groupby(daily_iter, key=lambda r: r["race_id"]):
            race = list(race_iter)
            active = [r for r in race if r["is_starter"] is True]
            n = len(active)
            supported = 8 <= n <= 18 and all(valid_run(r) for r in active)
            surface = "dirt" if str(race[0]["track_code"]).startswith("2") else "turf"
            strengths = [horse_long_front_strength(list(horses[str(r["horse_id"])])) for r in active]
            for i, r in enumerate(active):
                h = str(r["horse_id"])
                history = list(last_runs[h])
                # Use the shared representative-style and first-corner descriptors.
                f = representative_pace_features(history)
                if day(race_day).year >= 2018:
                    representative_styles.append(
                        {
                            "race_id": r["race_id"],
                            "horse_id": h,
                            **{
                                k: f[k]
                                for k in (
                                    "representative_front",
                                    "representative_forward",
                                    "representative_corner1",
                                    "representative_style_known",
                                )
                            },
                        }
                    )
                early = next(
                    (
                        number(r.get(k))
                        for k in ("corner_1_position", "corner_2_position", "corner_3_position")
                        if number(r.get(k)) >= 1
                    ),
                    float("nan"),
                )
                fourth = number(r.get("corner_4_position"))
                if (
                    supported
                    and math.isfinite(early + fourth)
                    and str(r["track_code"])[:1] in {"1", "2"}
                    and day(race_day).year >= 2018
                ):
                    values = expanded_position_features(
                        list(horses[h]),
                        list(jockeys[str(r["jockey_id"])]),
                        race_date=day(race_day),
                        field_size=n,
                        horse_number=int(r["horse_number"]),
                        horse_age=r["horse_age"],
                        surface=surface,
                        distance=int(r["distance_meters"]),
                        venue_code=str(r["venue_code"]),
                        other_front_pressure=(sum(strengths) - strengths[i]) / (n - 1),
                    )
                    row = {
                        "race_id": r["race_id"],
                        "race_date": str(race_day),
                        "horse_number": r["horse_number"],
                        "field_size": n,
                        "early": (n - early) / (n - 1),
                        "fourth": (n - fourth) / (n - 1),
                        "early_rank": early,
                        "fourth_rank": fourth,
                        "rank_gain": early - fourth,
                        "makuri": float(early > 3 and fourth <= 3 and early - fourth >= 3),
                        **{f"x{j}": x for j, x in enumerate(values)},
                    }
                    for stage in ("early", "fourth"):
                        row[f"bundled_{stage}"], row[f"bundled_{stage}_lead"] = predict_hybrid_position(values, stage)
                    output_rows.append(row)
                if math.isfinite(early):
                    style_mismatch["runs"] += 1
                    style_mismatch["representative_front_but_not_early_lead"] += int(
                        str(r.get("running_style_code")) == "1" and early != 1
                    )
                    style_mismatch["early_lead_but_other_style"] += int(
                        early == 1 and str(r.get("running_style_code")) != "1"
                    )
                    style_mismatch["late_advance_to_front"] += int(
                        math.isfinite(fourth) and early > 3 and fourth <= 3 and early - fourth >= 3
                    )
                elif math.isfinite(fourth):
                    style_mismatch["only_fourth_recorded"] += 1
            pending.extend(race)
        for r in pending:
            if valid_run(r):
                last_runs[str(r["horse_id"])].appendleft(enrich_run(r))
            n = number(r.get("starters"))
            first = next(
                (
                    number(r.get(k))
                    for k in ("corner_1_position", "corner_2_position", "corner_3_position", "corner_4_position")
                    if number(r.get(k)) >= 1
                ),
                float("nan"),
            )
            fourth = number(r.get("corner_4_position"))
            if (
                valid_run(r)
                and str(r.get("track_code"))[:1] in {"1", "2"}
                and n >= 2
                and math.isfinite(first + fourth)
            ):
                norm = {
                    "early": (n - first) / (n - 1),
                    "fourth": (n - fourth) / (n - 1),
                    "lead": float(first == 1),
                    "top3": float(first <= 3),
                    "surface": "dirt" if str(r["track_code"]).startswith("2") else "turf",
                    "distance": int(r["distance_meters"]),
                    "race_date": day(r["race_date"]),
                }
                horses[str(r["horse_id"])].appendleft(norm)
                jockeys[str(r["jockey_id"])].appendleft(norm)
    pd.DataFrame(output_rows).to_parquet(path, index=False)
    pd.DataFrame(representative_styles).to_parquet(output / "representative-pace-features.parquet", index=False)
    write_json(
        output / "style-definition-audit.json",
        {
            **style_mismatch,
            "makuri_definition": "descriptive proxy: first recorded corner1-3 >3, fourth<=3, gain>=3; not proof of intent",
            "source_scope": "2010 onward JRA observations; representative labels cannot be substituted for early tactics",
        },
    )
    return path


def _pace_probabilities(
    prediction: np.ndarray, mu: np.ndarray, course_sd: np.ndarray, residual_sd: float
) -> np.ndarray:
    from scipy.special import ndtr

    slow = 1 - ndtr((mu + 0.5 * course_sd - prediction) / residual_sd)
    high = ndtr((mu - 0.5 * course_sd - prediction) / residual_sd)
    return np.maximum(np.c_[slow, 1 - slow - high, high], 1e-15)


def period_masks(frame: Any, periods: dict[str, tuple[str, str]]) -> dict[str, np.ndarray]:
    masks = {
        name: ((frame.race_date >= start) & (frame.race_date < end)).to_numpy()
        for name, (start, end) in periods.items()
    }
    validate_partitions({name: frame.loc[mask] for name, mask in masks.items()})
    return masks


def study_pace(features: Path, output: Path, periods: dict[str, tuple[str, str]]) -> dict[str, Any]:
    import pandas as pd
    from catboost import CatBoostRegressor
    from lightgbm import LGBMRegressor
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    horse = pd.read_parquet(features)
    baseline = pd.read_parquet(output / "representative-pace-features.parquet")
    horse = horse.merge(baseline, on=["race_id", "horse_id"], validate="one_to_one")
    race_columns = [
        "field_size",
        "distance",
        "dirt",
        "venue",
        "grade",
        "course_pace_mean",
        "course_pace_std",
        "course_pace_count",
    ]
    mean_columns = [
        "early_frontness",
        "fourth_frontness",
        "early_lead_rate",
        "early_top3_rate",
        "rank_gain",
        "speed_quality_raw",
        "recent_form_raw",
        "corner_count",
        "representative_front",
        "representative_forward",
        "representative_corner1",
        "representative_style_known",
    ]
    group = horse.groupby("race_id", sort=False)
    races = (
        group[["race_date", "pace_class", "pace_seconds", *race_columns]]
        .first()
        .join(group[mean_columns].mean())
        .reset_index()
    )
    races = (
        races[(races.pace_class >= 0) & (races.course_pace_count >= 30)]
        .sort_values(["race_date", "race_id"])
        .reset_index(drop=True)
    )
    xcols = [*race_columns, *mean_columns[:8]]
    masks = period_masks(races, periods)
    train, select, cal, assessment = (masks[name] for name in ("train", "selection", "calibration", "assessment"))
    medians = races.loc[train, xcols].median().fillna(0)
    x = races[xcols].fillna(medians).to_numpy()
    y = races.pace_seconds.to_numpy()
    baseline_pace_z = (
        -3.772402
        + 1.513297 * races.representative_front
        + 1.803313 * races.representative_forward
        + 1.783288 * races.representative_corner1
        + 1.153411 * races.representative_style_known
        + 1.474942 * races.field_size / 18
    ).to_numpy()
    mu, sd = races.course_pace_mean.to_numpy(), races.course_pace_std.to_numpy()
    candidates = {
        "bundled_linear": None,
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=100)),
        "lightgbm": LGBMRegressor(
            n_estimators=450,
            num_leaves=15,
            min_child_samples=60,
            learning_rate=0.03,
            reg_lambda=10,
            n_jobs=4,
            verbosity=-1,
            random_state=617,
        ),
        "catboost": CatBoostRegressor(
            iterations=600,
            depth=5,
            learning_rate=0.04,
            l2_leaf_reg=10,
            thread_count=4,
            random_seed=617,
            verbose=False,
            allow_writing_files=False,
        ),
    }
    report: dict[str, Any] = {
        "target": "raw first3F seconds; slow/middle/high relative to strictly prior course mean +/-0.5sd",
        "bundled_baseline": "same shipped pace coefficients, descriptor definitions unchanged; Gaussian residual calibration added for comparable probability scoring",
        "candidates": {},
        "production": "retain_bundled_pending_probability_gate",
    }
    predictions = {}
    for name, model in candidates.items():
        if model is None:
            prediction = mu - baseline_pace_z * sd
        else:
            model.fit(x[train], y[train])
            prediction = model.predict(x)
        predictions[name] = prediction
        sigma_selection = max(0.1, float(np.std(y[select] - prediction[select])))
        probs = _pace_probabilities(prediction[select], mu[select], sd[select], sigma_selection)
        report["candidates"][name] = {
            "selection": pace_metrics(races.pace_class.to_numpy()[select], probs, y[select], prediction[select])
        }
    chosen = min(candidates, key=lambda n: report["candidates"][n]["selection"]["log_loss"])
    report["chosen_on_selection"] = chosen
    for name, prediction in predictions.items():
        sigma = max(0.1, float(np.std(y[cal] - prediction[cal])))
        probs = _pace_probabilities(prediction[assessment], mu[assessment], sd[assessment], sigma)
        report["candidates"][name]["assessment"] = pace_metrics(
            races.pace_class.to_numpy()[assessment], probs, y[assessment], prediction[assessment]
        )
        report["candidates"][name]["residual_sd_calibration"] = sigma
    # Probability scores, macro F1 and seconds error must all improve. Limited validation cannot promote a new pace model.
    report["adoption"] = (
        "retain_existing; ancillary assessment sample and cross-fold probability evidence insufficient for promotion"
    )
    write_json(output / "pace-study.json", report)
    return report


def _position_probability(
    quality: np.ndarray, lead: np.ndarray, temperature: float, sizes: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(617)
    marginals, expectations = [], []
    start = 0
    for size in sizes:
        n = int(size)
        q, p = quality[start : start + n], lead[start : start + n]
        p = np.maximum(p, 1e-12)
        p /= p.sum()
        leaders = rng.choice(n, size=1200, p=p)
        scores = q[None, :] / temperature + rng.gumbel(size=(1200, n))
        scores[np.arange(1200), leaders] = np.inf
        ranks = np.argsort(np.argsort(-scores, axis=1), axis=1) + 1
        marginals.extend([np.mean(ranks[:, i, None] == np.arange(1, n + 1)[None, :], axis=0) for i in range(n)])
        expectations.extend(ranks.mean(axis=0).tolist())
        start += n
    return np.asarray(expectations), np.asarray(marginals, dtype=object)


def study_positions(path: Path, output: Path, periods: dict[str, tuple[str, str]]) -> dict[str, Any]:
    import pandas as pd
    from catboost import CatBoostRegressor
    from lightgbm import LGBMRegressor
    from sklearn.linear_model import Ridge

    from jvdata_bridge_mcp.tools.prediction._positions_coefficients import POSITION_HYBRID_MODELS

    frame = pd.read_parquet(path).sort_values(["race_date", "race_id", "horse_number"]).reset_index(drop=True)
    x = frame[[f"x{i}" for i in range(38)]].to_numpy()
    masks = period_masks(frame, periods)
    train, select, assessment = (masks[name] for name in ("train", "selection", "assessment"))
    report: dict[str, Any] = {
        "scope": "8-18 classified finishers; missing early corners excluded; JRA warmup since 2010",
        "bundled_exposure": "existing coefficients used 2012-2024 for fit and saw 2025-2026; pre-2025 scores are not out-of-time for that baseline",
        "periods": periods,
        "stages": {},
        "production": "retain_existing; no independently verified joint pace/position improvement",
    }
    chosen_predictions = {}
    for stage in ("early", "fourth"):
        y = frame[stage].to_numpy()
        models = {
            "baseline": None,
            "ridge": Ridge(alpha=100),
            "lightgbm": LGBMRegressor(
                n_estimators=350,
                num_leaves=15,
                learning_rate=0.04,
                min_child_samples=150,
                n_jobs=4,
                verbosity=-1,
                random_state=617,
            ),
            "catboost": CatBoostRegressor(
                iterations=450,
                depth=5,
                learning_rate=0.04,
                l2_leaf_reg=10,
                thread_count=4,
                verbose=False,
                random_seed=617,
                allow_writing_files=False,
            ),
        }
        results, predictions = {}, {}
        for name, model in models.items():
            if model is None:
                prediction = frame[f"bundled_{stage}"].to_numpy()
            else:
                model.fit(x[train], y[train])
                prediction = np.clip(model.predict(x), 0, 1)
            predictions[name] = prediction
            # First-stage regression metric on actual rank scale, not normalized quality.
            error = np.abs(prediction - y) * (frame.field_size.to_numpy() - 1)
            results[name] = {
                "selection_rank_mae": float(error[select].mean()),
                "assessment_raw_rank_mae": float(error[assessment].mean()),
            }
        chosen = min(models, key=lambda n: results[n]["selection_rank_mae"])
        chosen_predictions[stage] = predictions[chosen]
        for name in set((chosen, "baseline")):
            pred = predictions[name]
            # Retain the shipped leader classifier for all rank candidates to isolate rank changes.
            leads = frame[f"bundled_{stage}_lead"].to_numpy()
            field = frame[assessment]
            sizes = field.groupby("race_id", sort=False).size().to_numpy()
            expected, dist = _position_probability(
                pred[assessment], leads[assessment], POSITION_HYBRID_MODELS[stage].temperature, sizes
            )
            rank = field[f"{stage}_rank"].to_numpy()
            top3 = np.asarray([sum(row[:3]) for row in dist], dtype=float)
            lead_p = np.asarray([row[0] for row in dist], dtype=float)
            observed = (rank <= 3).astype(float)
            groups_test = field.assign(predicted=top3).groupby("race_id", sort=False)
            recall = mean(
                [float((r.nlargest(3, "predicted")[f"{stage}_rank"] <= 3).sum() / 3) for _, r in groups_test]
            )
            results[name].update(
                {
                    "assessment_expected_rank_mae": float(np.abs(expected - rank).mean()),
                    "front3_recall": recall,
                    "front3_brier": float(np.mean((top3 - observed) ** 2)),
                    "lead_brier": float(np.mean((lead_p - (rank == 1)) ** 2)),
                    "top3_calibration": calibration_curve(top3, observed),
                    "lead_calibration": calibration_curve(lead_p, (rank == 1).astype(float)),
                    "assessment_races": int(field.race_id.nunique()),
                }
            )
        report["stages"][stage] = {"chosen_on_selection": chosen, "candidates": results}
    predicted_gain = (chosen_predictions["fourth"] - chosen_predictions["early"]) * (frame.field_size.to_numpy() - 1)
    report["rank_gain_mae"] = float(np.abs(predicted_gain[assessment] - frame.rank_gain.to_numpy()[assessment]).mean())
    report["makuri_proxy_rate_assessment"] = float(frame.makuri.to_numpy()[assessment].mean())
    write_json(output / "position-study.json", report)
    return report


def study_pace_position_consistency(
    output: Path, features: Path, periods: dict[str, tuple[str, str]]
) -> dict[str, Any]:
    """同じ最終レースでペースと位置の誤差を層別化する。同時分布の較正とは区別。"""
    import pandas as pd

    position = pd.read_parquet(output / "positions.parquet")
    features = pd.read_parquet(features)
    descriptors = pd.read_parquet(output / "representative-pace-features.parquet")
    pace = features.merge(descriptors, on=["race_id", "horse_id"], validate="one_to_one")
    fields = pace.groupby("race_id", sort=False).agg(
        race_date=("race_date", "first"),
        observed_z=("pace_z", "first"),
        observed_class=("pace_class", "first"),
        front=("representative_front", "mean"),
        forward=("representative_forward", "mean"),
        corner1=("representative_corner1", "mean"),
        coverage=("representative_style_known", "mean"),
        field_size=("field_size", "first"),
    )
    fields["predicted_z"] = (
        -3.772402
        + 1.513297 * fields.front
        + 1.803313 * fields.forward
        + 1.783288 * fields.corner1
        + 1.153411 * fields.coverage
        + 1.474942 * fields.field_size / 18
    )
    start, end = periods["assessment"]
    assessment = position[(position.race_date >= start) & (position.race_date < end)].merge(
        fields[["observed_class", "observed_z", "predicted_z"]],
        left_on="race_id",
        right_index=True,
        validate="many_to_one",
    )
    assessment = assessment[assessment.observed_class >= 0]
    # Leader probabilities are normalized within each stage, as in the deployed simulation.
    assessment["lead_p"] = assessment.bundled_early_lead / assessment.groupby(
        "race_id", sort=False
    ).bundled_early_lead.transform("sum")
    assessment["lead_error"] = (assessment.lead_p - (assessment.early_rank == 1).astype(float)) ** 2
    assessment["early_error"] = np.abs(assessment.bundled_early - assessment.early) * (assessment.field_size - 1)
    assessment["fourth_error"] = np.abs(assessment.bundled_fourth - assessment.fourth) * (assessment.field_size - 1)
    assessment["gain_error"] = np.abs(
        (assessment.bundled_fourth - assessment.bundled_early) * (assessment.field_size - 1) - assessment.rank_gain
    )
    by_class = {}
    for code, label in ((0, "slow"), (1, "middle"), (2, "high")):
        subset = assessment[assessment.observed_class == code]
        by_class[label] = {
            "races": int(subset.race_id.nunique()),
            "runners": len(subset),
            "early_raw_rank_mae": float(subset.early_error.mean()),
            "fourth_raw_rank_mae": float(subset.fourth_error.mean()),
            "rank_gain_mae": float(subset.gain_error.mean()),
            "early_lead_brier": float(subset.lead_error.mean()),
        }
    race = assessment.groupby("race_id", sort=False).agg(
        observed_z=("observed_z", "first"),
        predicted_z=("predicted_z", "first"),
        lead_concentration=("lead_p", lambda values: float(np.sum(values**2))),
    )
    report = {
        "scope": "same assessment races; pace-conditioned error audit of retained production coefficients",
        "races": len(race),
        "by_observed_pace": by_class,
        "predicted_vs_observed_pace_correlation": float(race.predicted_z.corr(race.observed_z)),
        "pace_vs_leader_concentration_correlation": float(race.predicted_z.corr(race.lead_concentration)),
        "joint_calibration_status": "not_established; separate marginals do not define joint pace/tactical probabilities",
        "adoption": "retained_coefficients; no promotion based on descriptive correlations",
    }
    write_json(output / "pace-position-consistency.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--study", type=Path, required=True)
    args = parser.parse_args()
    study = archive_directory(args.study)
    protocol = json.loads((study / "protocol.json").read_bytes())
    ranges = fold_ranges(protocol["outer_development_years"][-1])
    periods = {
        "train": ranges["train"],
        "selection": ranges["selection"],
        "calibration": ranges["final_calibration"],
        "assessment": (ranges["assessment"][0], protocol["development_end_exclusive"]),
    }
    output = archive_directory(study / "ancillary")
    positions = positional_dataset(args.source, output)
    features = study / "reference/features.parquet"
    study_pace(features, output, periods)
    study_positions(positions, output, periods)
    study_pace_position_consistency(output, features, periods)


if __name__ == "__main__":
    main()
