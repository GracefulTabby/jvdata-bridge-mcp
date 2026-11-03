"""開発期間の線形対照・SQL・市場との比較、較正、計算資源を記録する。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from jvdata_bridge_mcp.ml._development import archive_directory, digest_file, write_json
from jvdata_bridge_mcp.ml.features import nullable_values
from jvdata_bridge_mcp.ml.inference import predict_probabilities
from jvdata_bridge_mcp.ml.research.common import fold_ranges, load_native, subset
from jvdata_bridge_mcp.ml.research.evaluation import common_rosters, conditions, monthly_comparisons
from jvdata_bridge_mcp.ml.research.fitting import check_export
from jvdata_bridge_mcp.ml.research.metrics import paired_day_interval, probability_metrics


def compare(
    frame: pd.DataFrame,
    current: np.ndarray,
    previous: np.ndarray,
    powers: list[float],
    previous_powers: list[float] | None = None,
    *,
    joint: bool = True,
) -> dict[str, Any]:
    candidate = probability_metrics(frame, current, stage_powers=powers, joint=joint)
    baseline = probability_metrics(frame, previous, stage_powers=previous_powers or [1, 1], joint=joint)
    dates = dict(zip(frame.race_id, frame.race_date, strict=True))
    return {
        "candidate": candidate,
        "baseline": baseline,
        "candidate_minus_baseline": {
            name: paired_day_interval(candidate, baseline, name, dates)
            for name in (("log_loss", "brier", "top3_log_loss", "joint_log_loss") if joint else ("log_loss", "brier"))
        },
    }


def quantile_diagnostics(frame: pd.DataFrame, p: np.ndarray) -> dict[str, Any]:
    labels = frame.win.to_numpy() / frame.groupby("race_id", sort=False).win.transform("sum").to_numpy()
    bins = np.unique(np.r_[0, np.quantile(p, np.linspace(0, 1, 11)), 1])
    rows = []
    for index, (low, high) in enumerate(zip(bins[:-1], bins[1:], strict=True)):
        mask = (p >= low) & ((p <= high) if index == len(bins) - 2 else (p < high))
        if mask.any():
            rows.append(
                {
                    "low": float(low),
                    "high": float(high),
                    "count": int(mask.sum()),
                    "predicted": float(p[mask].mean()),
                    "observed": float(labels[mask].mean()),
                    "squared_error": float(np.mean((p[mask] - labels[mask]) ** 2)),
                }
            )
    return {
        "quantile_bins": rows,
        "interpretation": "descriptive probability bands; not an uncertainty guarantee or a new calibration fit",
    }


def portable_parity(output: Path, frame: pd.DataFrame, study: dict[str, Any]) -> None:
    checks = {}
    for fold in study["folds"]:
        year = fold["year"]
        selection = subset(frame, fold_ranges(year)["selection"])
        for record in fold["selection_records"]:
            folder = output / f"fold-{year}" / record["trial"]["name"]
            payload = json.loads((folder / "model.json").read_bytes())
            native = load_native(payload, folder)
            checks[f"{year}/{record['trial']['name']}"] = check_export(native, payload, selection)
        scores = pd.read_parquet(output / f"fold-{year}/assessment-scores.parquet")
        races = scores.race_id.unique()
        chosen = races[np.unique(np.linspace(0, len(races) - 1, 20).astype(int))]
        payload = json.loads((output / f"fold-{year}/refreshed_fit/model.json").read_bytes())
        error = 0.0
        for race_id in chosen:
            rows = frame[frame.race_id == race_id].sort_values("horse_number")
            old = scores[scores.race_id == race_id].sort_values("horse_number")
            assert rows.horse_id.tolist() == old.horse_id.tolist()
            p = predict_probabilities(payload, rows[list(payload["features"])].to_dict("records"))
            error = max(error, float(np.max(np.abs(p - old.refreshed_fit.to_numpy()))))
        if error > 1e-8:
            raise ValueError(f"refreshed probability parity failed: {year}: {error}")
        checks[f"{year}/refreshed"] = error
    write_json(
        output / "portable-parity.json",
        {
            "passed": True,
            "candidate_sha256": study["candidate"]["sha256"],
            "errors": checks,
            "checks": len(checks),
            "maximum_error": max(checks.values()),
        },
    )


def benchmarks(output: Path, frame: pd.DataFrame, reference: Path, previous: Path | None = None) -> None:
    folder = archive_directory(output / "resources")
    valid = frame.groupby("race_id", sort=False).speed_missing.max().eq(0)
    rows = frame[frame.race_id.isin(valid[valid].index)]
    sizes = rows.groupby("race_id", sort=False).size()
    selected = sizes[sizes == 18].index[-1]
    sample = rows[rows.race_id == selected].copy()
    old = pd.read_parquet(reference / "features.parquet")
    sample = sample.merge(
        old[["race_id", "horse_id", "historical_market_score"]], on=["race_id", "horse_id"], validate="one_to_one"
    )
    rows_path = folder / "shared-rows.json"
    write_json(rows_path, nullable_values(sample.to_dict("records")))
    from jvdata_bridge_mcp.tools.prediction._indicator_sql import DEFAULT_INDICATOR_WEIGHTS, SPEED_QUALITY_WEIGHT

    weights = {name.removesuffix("_weight") + "_score": value for name, value in DEFAULT_INDICATOR_WEIGHTS.items()}
    weights["speed_quality_score"] = SPEED_QUALITY_WEIGHT
    baseline = folder / "baseline.json"
    write_json(baseline, {"kind": "bundled_linear", "weights": weights})
    paths = [baseline, output / "frozen-optimized-candidate.json"]
    if previous is not None:
        paths.append(previous / "frozen-optimized-candidate.json")
    paths.extend(sorted(output.glob("fold-*/*/model.json")))
    records = {}
    for index, path in enumerate(paths):
        identity = {"model_sha256": digest_file(path), "rows_sha256": digest_file(rows_path)}
        cache = folder / f"model-{index:03d}.json"
        value = json.loads(cache.read_bytes()) if cache.exists() else {}
        if value.get("identity") != identity:
            result = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "jvdata_bridge_mcp.ml.research.benchmark",
                    "--model",
                    str(path),
                    "--rows",
                    str(rows_path),
                ],
                text=True,
            )
            value = {"identity": identity, **json.loads(result)}
            write_json(cache, value)
        records[str(path)] = value
    write_json(
        output / "resource-benchmarks.json",
        {"models": records, "scope": "isolated score/softmax only; DB feature construction and tickets excluded"},
    )


def analyze(output: Path, reference: Path, previous: Path | None = None) -> None:
    output = archive_directory(output)
    study = json.loads((output / "optimization.json").read_bytes())
    frame = pd.read_parquet(output / "data/features.parquet")
    result: dict[str, Any] = {"scope": "development only; candidate stays unapproved", "folds": []}
    for fold in study["folds"]:
        year = fold["year"]
        scores = pd.read_parquet(output / f"fold-{year}/assessment-scores.parquet")
        comparison = None
        if previous is not None:
            previous_scores = pd.read_parquet(previous / f"fold-{year}/assessment-scores.parquet")
            previous_fold = json.loads((previous / f"fold-{year}/assessment.json").read_bytes())
            common = common_rosters(scores, previous_scores, "horse_id")
            joined = scores[scores.race_id.isin(common)].merge(
                previous_scores[["race_id", "horse_id", "refreshed_fit"]].rename(
                    columns={"refreshed_fit": "previous_candidate"}
                ),
                on=["race_id", "horse_id"],
                validate="one_to_one",
            )
            joined = joined.sort_values(["race_date", "race_id", "horse_number"]).reset_index(drop=True)
            comparison = compare(
                joined,
                joined.refreshed_fit.to_numpy(),
                joined.previous_candidate.to_numpy(),
                fold["models"]["refreshed_fit"]["stage_powers"],
                previous_fold["models"]["refreshed_fit"]["stage_powers"],
            )
        result["folds"].append(
            {
                "year": year,
                "chosen": fold["chosen"],
                "compared_to_previous_candidate": comparison,
                "compared_to_refit_linear": fold["refreshed_minus_refit_linear"],
                "compared_to_base_same_window": fold["refreshed_minus_base_window_refit"],
                "conditions": {
                    "candidate": conditions(scores, frame, fold["models"]["refreshed_fit"]["metrics"]),
                    "refit_linear": conditions(
                        scores.assign(refreshed_fit=scores.refit_linear),
                        frame,
                        fold["models"]["refit_linear"]["metrics"],
                    ),
                },
                "probability_diagnostics": quantile_diagnostics(scores, scores.refreshed_fit.to_numpy()),
            }
        )
    latest = study["folds"][-1]
    scores = pd.read_parquet(output / f"fold-{latest['year']}/assessment-scores.parquet")
    powers = latest["models"]["refreshed_fit"]["stage_powers"]
    original = pd.read_parquet(reference / "exact-baseline.parquet")
    common = common_rosters(scores, original, "horse_id")
    joined = (
        scores[scores.race_id.isin(common)]
        .merge(original[["race_id", "horse_id", "win_probability"]], on=["race_id", "horse_id"], validate="one_to_one")
        .sort_values(["race_date", "race_id", "horse_number"])
        .reset_index(drop=True)
    )
    result["exact_sql_baseline"] = compare(
        joined, joined.refreshed_fit.to_numpy(), joined.win_probability.to_numpy(), powers
    )
    result["exact_sql_baseline_by_month"] = monthly_comparisons(result["exact_sql_baseline"], joined)
    odds = pd.read_parquet(reference / "market.parquet")
    if (
        odds.published_at.isna().any()
        or not (odds.published_at <= odds.decision_at).all()
        or not (odds.decision_at < odds.scheduled_start_at).all()
    ):
        raise ValueError("unverified market publication boundary")
    common = common_rosters(scores, odds, "horse_number")
    joined = (
        scores[scores.race_id.isin(common)]
        .merge(odds[["race_id", "horse_number", "win_odds"]], on=["race_id", "horse_number"], validate="one_to_one")
        .sort_values(["race_date", "race_id", "horse_number"])
        .reset_index(drop=True)
    )
    inverse = 1 / joined.win_odds.to_numpy(dtype=float)
    denominator = pd.Series(inverse).groupby(joined.race_id).transform("sum").to_numpy()
    result["market"] = {
        **compare(joined, joined.refreshed_fit.to_numpy(), inverse / denominator, powers, joint=False),
        "decision_boundary": "published by scheduled start minus10 minutes; no final odds fills",
        "historical_card_publication_versions": "unverified",
        "profitability_claim": False,
    }
    write_json(output / "development-analysis.json", result)
    portable_parity(output, frame, study)
    benchmarks(output, frame, reference, previous)
    write_json(
        output / "future-status.json",
        {"status": "pending_fixed_future_period", "labels_inspected": False, "production_adopted": False},
    )
    print("development analysis, parity and resource checks complete", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "reference"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    analyze(args.output, args.reference, args.previous)


if __name__ == "__main__":
    main()
