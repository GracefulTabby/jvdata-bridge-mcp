"""データ量と特徴量群を分離して比較する固定プロトコル。実データはarchiveだけに置く。"""

from __future__ import annotations

import argparse
import gc
import json
import time
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from jvdata_bridge_mcp.ml._development import archive_directory, digest_file, write_json
from jvdata_bridge_mcp.ml.expanded_features import EXTENSION_GROUPS, FEATURE_NAMES, FEATURE_VERSION
from jvdata_bridge_mcp.ml.research.calibration import winner_scores
from jvdata_bridge_mcp.ml.research.common import (
    calibrate_stages,
    fold_ranges,
    load_native,
    save_native,
    subset,
    validate_partitions,
)
from jvdata_bridge_mcp.ml.research.expanded_calibration import CALIBRATORS, apply, fit
from jvdata_bridge_mcp.ml.research.fitting import Trial, check_export, fit_trial, native_scores
from jvdata_bridge_mcp.ml.research.metrics import paired_day_interval, probability_metrics
from jvdata_bridge_mcp.tools._observation_scope import now_jst

EXTRA_NAMES = tuple(name for group in EXTENSION_GROUPS.values() for name in group)
SOURCE_ROOT = Path(__file__).resolve().parents[2]


def freeze(output: Path, development_end: date, final_start: date, final_end: date) -> None:
    output = archive_directory(output)
    today = now_jst().date()
    calibration_start = date(development_end.year, 7, 1)
    if not calibration_start < development_end <= today < final_start < final_end or development_end > final_start:
        raise ValueError("development must end after July 1 and by today; final period must start in the future")
    if development_end.year < 2023:
        raise ValueError("four folds require development_end in 2023 or later")
    path = output / "protocol.json"
    if any((output / name).exists() for name in ("protocol.json", "adoption-rule.json", "execution-plan.json")):
        raise ValueError("study protocol is already frozen")
    dataset = pd.read_parquet(output / "data/features.parquet", columns=["race_date"])
    if dataset.empty or (dataset.race_date >= development_end.isoformat()).any():
        raise ValueError("development dataset must precede development_end")
    write_json(
        path,
        {
            "version": "market-free-comparison-v1",
            "development_end_exclusive": development_end.isoformat(),
            "candidate_calibration_start": calibration_start.isoformat(),
            "previously_inspected_periods_are_development": True,
            "outer_development_years": list(range(development_end.year - 3, development_end.year + 1)),
            "training_start_years": [2018, 2015, 2012],
            "long_history_decay_half_life_years": 2,
            "window_selection": "fixed native-categorical LambdaRank; base57 and full87 crossed with 2012/2015/2018 and 2012decay; selection LL subject to linear Brier",
            "feature_ablation": "all30 extensions and four leave-one-group-out refits at selected window",
            "additional_families": [
                "linear_all",
                "lightgbm_binary_all",
                "catboost_small_all",
                "catboost_small_base",
                "catboost_flexible_all",
            ],
            "redundant_rank_scores": "compare dropping the ten linear-model rank-score columns for LightGBM and CatBoost",
            "calibrators": list(CALIBRATORS),
            "selection_rule": "calibrated race LL; no worse Brier than initial linear_base",
            "fold_stages": "train through Y-2; stop Jan-Mar, provisional calibration Apr-Jun, selection Jul-Sep, independent calibration Oct-Dec of Y-1; development Y",
            "final_candidate": "latest-fold selection; train before candidate_calibration_start, calibrate until development_end; fixed selected iterations",
            "ensemble": "not included; focus on information quality and bounded single models",
        },
    )
    write_json(
        output / "adoption-rule.json",
        {
            "protocol_sha256": digest_file(path),
            "final_start_inclusive": final_start.isoformat(),
            "final_end_exclusive": final_end.isoformat(),
            "minimum_common_races": 200,
            "required_development_folds": 4,
            "minimum_improving_folds": 3,
            "resampling_unit": "race_date",
            "winner_log_loss_upper_95_below_zero": True,
            "brier_top3_joint_nonregression": True,
            "frozen_before_outer_assessment": False,
            "development_exposure": "all outer periods are previously inspected development; only future final is independent",
        },
    )
    plan = {
        "protocol_sha256": digest_file(path),
        "adoption_rule_sha256": digest_file(output / "adoption-rule.json"),
        "dataset_sha256": digest_file(output / "data/features.parquet"),
        "source_sha256": {
            path.relative_to(SOURCE_ROOT).as_posix(): digest_file(path) for path in sorted(SOURCE_ROOT.rglob("*.py"))
        },
    }
    write_json(output / "execution-plan.json", plan)


def load(output: Path) -> tuple[pd.DataFrame, dict[str, Any], str]:
    protocol = json.loads((output / "protocol.json").read_bytes())
    plan = json.loads((output / "execution-plan.json").read_bytes())
    if plan["protocol_sha256"] != digest_file(output / "protocol.json"):
        raise ValueError("protocol changed")
    if plan["adoption_rule_sha256"] != digest_file(output / "adoption-rule.json"):
        raise ValueError("adoption rule changed")
    for name, expected in plan["source_sha256"].items():
        if digest_file(SOURCE_ROOT / name) != expected:
            raise ValueError(f"source changed after freezing: {name}")
    path = output / "data/features.parquet"
    if digest_file(path) != plan["dataset_sha256"]:
        raise ValueError("dataset changed after freezing")
    frame = pd.read_parquet(path)
    if (
        frame.duplicated(["race_id", "horse_id"]).any()
        or (frame.history_max_date.notna() & (frame.history_max_date >= frame.race_date)).any()
    ):
        raise ValueError("duplicate or leaking dataset")
    if (frame.race_date >= protocol["development_end_exclusive"]).any():
        raise ValueError("uninspected final labels must not enter development")
    return frame, protocol, digest_file(output / "protocol.json")


def calibrations(
    model: Any, payload: dict[str, Any], calibration: pd.DataFrame, selection: pd.DataFrame
) -> dict[str, Any]:
    cal_scores, selected_scores = native_scores(model, payload, calibration), native_scores(model, payload, selection)
    result = {}
    for kind in CALIBRATORS:
        fitted = fit(cal_scores, calibration, kind)
        p = apply(selected_scores, selection, fitted)
        result[kind] = {**winner_scores(selection, p), "fitted": fitted}
    return result


def trial_selection(
    frame: pd.DataFrame,
    output: Path,
    year: int,
    trial: Trial,
    start: int,
    disabled: tuple[str, ...],
    protocol_hash: str,
) -> dict[str, Any]:
    folder = archive_directory(output / f"fold-{year}" / trial.name)
    identity = {"trial": asdict(trial), "training_start": start, "disabled": list(disabled), "protocol": protocol_hash}
    path = folder / "selection.json"
    if path.exists():
        record = json.loads(path.read_bytes())
        if record["identity"] != identity:
            raise ValueError("cached trial identity changed")
        return record
    ranges = fold_ranges(year)
    parts = {
        name: subset(frame, interval)
        for name, interval in ranges.items()
        if name in ("stopping", "provisional_calibration", "selection")
    }
    parts = {"train": subset(frame, (f"{start}-01-01", ranges["train"][1])), **parts}
    validate_partitions(parts)
    fitted_path = folder / "fit.json"
    if fitted_path.exists():
        fitted = json.loads(fitted_path.read_bytes())
        if fitted["identity"] != identity:
            raise ValueError("cached native fit identity changed")
        payload = json.loads((folder / "model.json").read_bytes())
        model, best, duration = load_native(payload, folder), fitted["best_iteration"], fitted["fit_seconds"]
    else:
        print("fit", year, trial.name, len(parts["train"]), flush=True)
        began = time.perf_counter()
        model, payload, best = fit_trial(
            parts["train"],
            parts["stopping"],
            trial,
            feature_names=FEATURE_NAMES,
            feature_version=FEATURE_VERSION,
            disabled_features=disabled,
        )
        duration = time.perf_counter() - began
        write_json(folder / "model.json", payload)
        save_native(model, payload, folder)
        write_json(fitted_path, {"identity": identity, "best_iteration": best, "fit_seconds": duration})
    error = check_export(model, payload, parts["selection"])
    records = calibrations(model, payload, parts["provisional_calibration"], parts["selection"])
    result = {
        "identity": identity,
        "trial": asdict(trial),
        "training_start": start,
        "disabled": list(disabled),
        "best_iteration": best,
        "fit_seconds": duration,
        "json_bytes": (folder / "model.json").stat().st_size,
        "native_export_max_error": error,
        "calibrations": records,
        "periods": {
            name: {
                "start": str(part.race_date.min()),
                "end": str(part.race_date.max()),
                "races": int(part.race_id.nunique()),
            }
            for name, part in parts.items()
        },
    }
    write_json(path, result)
    print("selection", year, trial.name, round(min(m["log_loss"] for m in records.values()), 6), flush=True)
    del model, parts
    gc.collect()
    return result


def choose(records: list[dict[str, Any]], baseline: dict[str, Any]) -> dict[str, Any]:
    brier = min(baseline["calibrations"].values(), key=lambda item: item["log_loss"])["brier"]
    choices = [
        {"record": record, "calibration": kind, **scores}
        for record in records
        for kind, scores in record["calibrations"].items()
        if scores["brier"] <= brier + 1e-12
    ]
    if not choices:
        raise ValueError("no admissible calibrated candidate")
    return min(
        choices, key=lambda item: (item["log_loss"], item["record"]["json_bytes"], item["record"]["fit_seconds"])
    )


def selected_pipeline(
    frame: pd.DataFrame, output: Path, year: int, protocol_hash: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    linear = Trial("linear_base_2018", "linear", regularization=0.0005)
    baseline = trial_selection(frame, output, year, linear, 2018, EXTRA_NAMES, protocol_hash)
    records = [baseline]
    prototype = Trial("base", "lightgbm", missing="native", categorical=True, ranking=True)
    for start in (2018, 2015, 2012):
        records.append(
            trial_selection(
                frame, output, year, replace(prototype, name=f"base_{start}"), start, EXTRA_NAMES, protocol_hash
            )
        )
    records.append(
        trial_selection(
            frame,
            output,
            year,
            replace(prototype, name="base_2012_decay", half_life_years=2),
            2012,
            EXTRA_NAMES,
            protocol_hash,
        )
    )
    for start, half_life in ((2018, 0), (2015, 0), (2012, 0), (2012, 2)):
        records.append(
            trial_selection(
                frame,
                output,
                year,
                replace(prototype, name=f"all_{start}" + ("_decay" if half_life else ""), half_life_years=half_life),
                start,
                (),
                protocol_hash,
            )
        )
    full_records = [record for record in records if record["trial"]["name"].startswith("all_")]
    admissible_full = [
        record
        for record in full_records
        if any(
            score["brier"]
            <= min(baseline["calibrations"].values(), key=lambda item: item["log_loss"])["brier"] + 1e-12
            for score in record["calibrations"].values()
        )
    ]
    window = choose(admissible_full or records, baseline)
    window_record = window["record"]
    start = window_record["training_start"]
    half_life = window_record["trial"]["half_life_years"]
    prototype = replace(prototype, half_life_years=half_life)
    for group, names in EXTENSION_GROUPS.items():
        records.append(
            trial_selection(
                frame, output, year, replace(prototype, name=f"without_{group}"), start, names, protocol_hash
            )
        )
    records.append(
        trial_selection(
            frame, output, year, replace(prototype, name="binary_all", ranking=False), start, (), protocol_hash
        )
    )
    cat = Trial(
        "cat_all",
        "catboost",
        missing="native",
        categorical=True,
        leaves_or_depth=5,
        regularization=20,
        half_life_years=half_life,
    )
    records.append(trial_selection(frame, output, year, cat, start, (), protocol_hash))
    records.append(
        trial_selection(
            frame,
            output,
            year,
            replace(cat, name="cat_flexible_all", leaves_or_depth=8, learning_rate=0.025),
            start,
            (),
            protocol_hash,
        )
    )
    raw_only = tuple(name for name in FEATURE_NAMES[:57] if name.endswith("_score"))
    records.append(trial_selection(frame, output, year, replace(cat, name="cat_raw"), start, raw_only, protocol_hash))
    records.append(
        trial_selection(frame, output, year, replace(prototype, name="lgb_raw"), start, raw_only, protocol_hash)
    )
    records.append(
        trial_selection(frame, output, year, replace(cat, name="cat_base"), start, EXTRA_NAMES, protocol_hash)
    )
    records.append(
        trial_selection(
            frame,
            output,
            year,
            replace(linear, name="linear_all", half_life_years=half_life),
            start,
            (),
            protocol_hash,
        )
    )
    choice = choose(records, baseline)
    selected = choice["record"]
    control = next(
        record
        for record in records
        if record["trial"]["name"].startswith("base_")
        and record["training_start"] == selected["training_start"]
        and record["trial"]["half_life_years"] == selected["trial"]["half_life_years"]
    )
    kind, metric = min(control["calibrations"].items(), key=lambda item: item[1]["log_loss"])
    window_control = {"record": control, "calibration": kind, **metric}
    return choice, window_control, choose([baseline], baseline), records


def refit(
    frame: pd.DataFrame, choice: dict[str, Any], folder: Path, train_end: str, calibration_interval: tuple[str, str]
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    record = choice["record"]
    training = subset(frame, (f"{record['training_start']}-01-01", train_end))
    calibration_frame = subset(frame, calibration_interval)
    validate_partitions({"train": training, "calibration": calibration_frame})
    path = folder / "model.json"
    if path.exists():
        payload = json.loads(path.read_bytes())
        return load_native(payload, folder), payload, payload["calibration"]
    model, payload, _ = fit_trial(
        training,
        calibration_frame,
        Trial(**record["trial"]),
        iterations=record["best_iteration"] or None,
        feature_names=FEATURE_NAMES,
        feature_version=FEATURE_VERSION,
        disabled_features=tuple(record["disabled"]),
    )
    scores = native_scores(model, payload, calibration_frame)
    calibration = fit(scores, calibration_frame, choice["calibration"])
    powers = calibrate_stages(apply(scores, calibration_frame, calibration), calibration_frame)
    payload.update(
        temperature=calibration["temperature"],
        field_temperature_slope=calibration["slope"],
        reliability_temperature_slope=calibration["reliability_slope"],
        stage_powers=powers,
        calibration=calibration,
    )
    error = check_export(model, payload, calibration_frame)
    payload["portable_export_error"] = error
    payload["fit_periods"] = {
        name: {
            "start": str(part.race_date.min()),
            "end": str(part.race_date.max()),
            "races": int(part.race_id.nunique()),
        }
        for name, part in (("training", training), ("calibration", calibration_frame))
    }
    folder.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)
    save_native(model, payload, folder)
    return model, payload, calibration


def run_fold(output: Path, year: int) -> None:
    frame, protocol, protocol_hash = load(output)
    if year not in protocol["outer_development_years"]:
        raise ValueError("year must belong to the frozen development folds")
    folder = output / f"fold-{year}"
    if (folder / "assessment.json").exists():
        print("fold already complete", year, flush=True)
        return
    choice, window, linear, records = selected_pipeline(frame, output, year, protocol_hash)
    write_json(folder / "chosen.json", {"choice": choice, "window": window, "linear": linear})
    ranges = fold_ranges(year)
    assessment = subset(frame, ranges["assessment"])
    scores = assessment[
        ["race_id", "race_date", "horse_id", "horse_number", "win", "finish", "venue_code", "track_code", "field_size"]
    ].copy()
    result: dict[str, Any] = {
        "year": year,
        "chosen": {
            "trial": choice["record"]["trial"],
            "training_start": choice["record"]["training_start"],
            "disabled": choice["record"]["disabled"],
            "calibration": choice["calibration"],
        },
        "period": {"start": str(assessment.race_date.min()), "end": str(assessment.race_date.max())},
        "selection_records": records,
        "models": {},
    }
    assessed: dict[str, tuple[Any, dict[str, Any]]] = {}
    for name, selected in (("refreshed_fit", choice), ("base_window_refit", window), ("refit_linear", linear)):
        identity = json.dumps(
            {"fit": selected["record"]["identity"], "calibration": selected["calibration"]}, sort_keys=True
        )
        if identity in assessed:
            probabilities, item = assessed[identity]
            result["models"][name] = item
            scores[name] = probabilities
            continue
        print("assess", year, name, selected["record"]["trial"]["name"], flush=True)
        model, payload, calibration = refit(
            frame, selected, folder / name, ranges["selection"][1], ranges["final_calibration"]
        )
        probabilities = apply(native_scores(model, payload, assessment), assessment, calibration)
        result["models"][name] = {
            "metrics": probability_metrics(assessment, probabilities, stage_powers=payload["stage_powers"]),
            "calibration": calibration,
            "stage_powers": payload["stage_powers"],
            "portable_error": payload["portable_export_error"],
        }
        scores[name] = probabilities
        assessed[identity] = probabilities, result["models"][name]
        del model
        gc.collect()
    dates = dict(zip(assessment.race_id, assessment.race_date, strict=True))
    for baseline in ("refit_linear", "base_window_refit"):
        result[f"refreshed_minus_{baseline}"] = {
            metric: paired_day_interval(
                result["models"]["refreshed_fit"]["metrics"], result["models"][baseline]["metrics"], metric, dates
            )
            for metric in ("log_loss", "brier", "top3_log_loss", "joint_log_loss")
        }
    scores.to_parquet(folder / "assessment-scores.parquet", index=False)
    write_json(folder / "assessment.json", result)
    print(
        "fold complete",
        year,
        {name: round(item["metrics"]["log_loss"], 6) for name, item in result["models"].items()},
        flush=True,
    )


def finalize(output: Path) -> None:
    frame, protocol, protocol_hash = load(output)
    rules = json.loads((output / "adoption-rule.json").read_bytes())
    if now_jst().date().isoformat() >= rules["final_start_inclusive"]:
        raise ValueError("candidate must be frozen before its independent final period")
    folds = [
        json.loads((output / f"fold-{year}/assessment.json").read_bytes())
        for year in protocol["outer_development_years"]
    ]
    latest = protocol["outer_development_years"][-1]
    chosen = json.loads((output / f"fold-{latest}/chosen.json").read_bytes())["choice"]
    model, payload, _ = refit(
        frame,
        chosen,
        output / "final-native",
        protocol["candidate_calibration_start"],
        (protocol["candidate_calibration_start"], protocol["development_end_exclusive"]),
    )
    del model
    payload.update(
        model_version=f"expanded-{chosen['record']['trial']['name']}-{chosen['record']['training_start']}",
        calibration_status="fitted_on_separate_period; independent_final_pending",
        provenance={
            "protocol_sha256": protocol_hash,
            "feature_dataset_sha256": digest_file(output / "data/features.parquet"),
            "periods": {
                **payload["fit_periods"],
                "selection": chosen["record"]["periods"]["selection"],
                "early_stopping": chosen["record"]["periods"]["stopping"],
                "provisional_calibration": chosen["record"]["periods"]["provisional_calibration"],
                "development_assessment": {
                    "start": min(fold["period"]["start"] for fold in folds),
                    "end": max(fold["period"]["end"] for fold in folds),
                    "status": "previously_inspected_development",
                },
                "independent_final": {
                    "start_inclusive": rules["final_start_inclusive"],
                    "end_exclusive": rules["final_end_exclusive"],
                    "status": "not_inspected",
                },
            },
            "historical_card_publication_versions": "unverified",
        },
        adoption={
            "approved_by_empirical_gate": False,
            "exact_baseline_required": True,
            "walk_forward_gate": False,
            "independent_final_gate": False,
            "reason": "independent_final_pending",
        },
    )
    candidate = output / "frozen-optimized-candidate.json"
    if candidate.exists():
        raise ValueError("frozen candidate cannot be overwritten")
    write_json(candidate, payload)
    write_json(
        output / "optimization.json",
        {
            "protocol": protocol,
            "execution_plan_sha256": digest_file(output / "execution-plan.json"),
            "folds": folds,
            "candidate": {
                "path": str(candidate),
                "sha256": digest_file(candidate),
                "bytes": candidate.stat().st_size,
                "native_export_max_error": payload["portable_export_error"],
            },
            "production_adopted": False,
        },
    )
    print("candidate frozen", payload["model_version"], candidate.stat().st_size, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("freeze", "fold", "finalize"), required=True)
    parser.add_argument("--year", type=int)
    parser.add_argument("--development-end", type=date.fromisoformat)
    parser.add_argument("--final-start", type=date.fromisoformat)
    parser.add_argument("--final-end", type=date.fromisoformat)
    args = parser.parse_args()
    if args.phase == "freeze":
        if any(value is None for value in (args.development_end, args.final_start, args.final_end)):
            parser.error("freeze requires --development-end, --final-start and --final-end")
        freeze(args.output, args.development_end, args.final_start, args.final_end)
    elif args.phase == "finalize":
        finalize(args.output)
    else:
        if args.year is None:
            parser.error("fold requires --year")
        run_fold(args.output, args.year)


if __name__ == "__main__":
    main()
