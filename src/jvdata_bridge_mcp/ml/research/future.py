"""固定した未使用期間だけで凍結候補を判定する。重みの再学習・再選択をしない。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from jvdata_bridge_mcp.ml._development import archive_directory, digest_file, write_json
from jvdata_bridge_mcp.ml.inference import predict_probabilities, validate_model
from jvdata_bridge_mcp.ml.research.metrics import paired_day_interval, probability_metrics
from jvdata_bridge_mcp.tools._observation_scope import now_jst


def walk_forward_passed(report: dict[str, Any], required_folds: int, minimum_improving: int) -> bool:
    folds = report["folds"]
    if len(folds) < required_folds:
        return False
    deltas = [fold["refreshed_minus_refit_linear"]["log_loss"]["mean_delta"] for fold in folds]
    if any(value is None for value in deltas):
        return False
    return sum(value < 0 for value in deltas) >= minimum_improving and float(np.mean(deltas)) < 0


def evaluate(output: Path, features: Path, baseline: Path) -> dict[str, Any]:
    output = archive_directory(output)
    rules = json.loads((output / "adoption-rule.json").read_text(encoding="utf-8"))
    if now_jst().date().isoformat() < rules["final_end_exclusive"]:
        return {"status": "pending_fixed_future_period", "labels_inspected": False, "production_adopted": False}
    model_path = output / "frozen-optimized-candidate.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    validate_model(model)
    study = json.loads((output / "optimization.json").read_text(encoding="utf-8"))
    plan_path = output / "execution-plan.json"
    if digest_file(plan_path) != study["execution_plan_sha256"]:
        raise ValueError("frozen execution plan changed")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    source_root = Path(__file__).resolve().parents[2]
    for name, expected in plan["source_sha256"].items():
        if digest_file(source_root / name) != expected:
            raise ValueError("feature or inference implementation changed after freezing")
    if plan.get("adoption_rule_sha256") != digest_file(output / "adoption-rule.json"):
        raise ValueError("adoption rule changed after freezing")
    if plan["protocol_sha256"] != digest_file(output / "protocol.json"):
        raise ValueError("protocol changed after freezing")
    digest = digest_file(model_path)
    if study["candidate"]["sha256"] != digest:
        raise ValueError("candidate changed after freezing")
    parity = json.loads((output / "portable-parity.json").read_text(encoding="utf-8"))
    if parity.get("passed") is not True or parity.get("candidate_sha256") != digest:
        raise ValueError("native/portable parity for this frozen study is required")
    final_period = model["provenance"]["periods"]["independent_final"]
    if (final_period["start_inclusive"], final_period["end_exclusive"]) != (
        rules["final_start_inclusive"],
        rules["final_end_exclusive"],
    ):
        raise ValueError("independent final boundary changed")
    manifest = json.loads((features.parent / "dataset.json").read_text(encoding="utf-8"))
    if manifest.get("feature_recipe", "reference") != model.get("feature_recipe", "reference"):
        raise ValueError("feature recipe differs from frozen model")
    if manifest.get("sha256") != digest_file(features):
        raise ValueError("feature dataset checksum mismatch")
    ids = pd.read_parquet(features, columns=["race_id", "race_date", "horse_id", "horse_number", "history_max_date"])
    ids = ids[(ids.race_date >= rules["final_start_inclusive"]) & (ids.race_date < rules["final_end_exclusive"])]
    if (
        ids.duplicated(["race_id", "horse_id"]).any()
        or (ids.history_max_date.notna() & (ids.history_max_date >= ids.race_date)).any()
    ):
        raise ValueError("duplicate or leaking final dataset")
    original = pd.read_parquet(baseline)
    if original.duplicated(["race_id", "horse_id"]).any():
        raise ValueError("duplicate original-model predictions")
    rosters = ids.groupby("race_id", sort=False).horse_id.agg(set)
    baseline_rosters = original.groupby("race_id", sort=False).horse_id.agg(set)
    common = {
        race for race, horses in rosters.items() if race in baseline_rosters and horses == baseline_rosters[race]
    }
    if len(common) < rules["minimum_common_races"]:
        return {
            "status": "insufficient_common_races",
            "common_races": len(common),
            "labels_inspected": False,
            "production_adopted": False,
        }
    exposure = output / "independent-final-exposure.json"
    identity = {
        "candidate_sha256": digest,
        "portable_parity_sha256": digest_file(output / "portable-parity.json"),
        "adoption_rule_sha256": digest_file(output / "adoption-rule.json"),
        "features_sha256": digest_file(features),
        "baseline_sha256": digest_file(baseline),
    }
    if exposure.exists() and json.loads(exposure.read_text(encoding="utf-8")) != identity:
        raise ValueError("final evaluation inputs changed after inspection; choose a new uninspected period")
    write_json(exposure, identity)
    frame = pd.read_parquet(features)
    frame = (
        frame[frame.race_id.isin(common)].sort_values(["race_date", "race_id", "horse_number"]).reset_index(drop=True)
    )
    merged = frame.merge(
        original[["race_id", "horse_id", "win_probability"]], on=["race_id", "horse_id"], validate="one_to_one"
    )
    merged = merged.sort_values(["race_date", "race_id", "horse_number"]).reset_index(drop=True)
    p = []
    for _, race in merged.groupby("race_id", sort=False):
        p.extend(predict_probabilities(model, race[list(model["features"])].to_dict("records")))
    candidate = probability_metrics(merged, np.asarray(p), stage_powers=model["stage_powers"])
    previous = probability_metrics(merged, merged.win_probability.to_numpy())
    dates = dict(zip(merged.race_id, merged.race_date, strict=True))
    comparisons = {
        key: paired_day_interval(candidate, previous, key, dates)
        for key in ("log_loss", "brier", "top3_log_loss", "joint_log_loss")
    }
    loss_interval = comparisons["log_loss"]["interval_95"]
    win_gate = loss_interval is not None and loss_interval[1] < 0
    supporting = all(
        comparisons[key]["mean_delta"] is not None and comparisons[key]["mean_delta"] <= 0
        for key in ("brier", "top3_log_loss", "joint_log_loss")
    )
    walk = walk_forward_passed(study, rules["required_development_folds"], rules["minimum_improving_folds"])
    approved = bool(win_gate and supporting and walk)
    adoption = {
        "approved_by_empirical_gate": approved,
        "exact_baseline_required": True,
        "walk_forward_gate": walk,
        "independent_final_gate": approved,
        "winner_gate": win_gate,
        "supporting_probability_gate": supporting,
    }
    report = {
        "status": "completed",
        "period": {
            "start_inclusive": rules["final_start_inclusive"],
            "end_exclusive": rules["final_end_exclusive"],
            "first_complete_race_date": str(merged.race_date.min()),
            "last_complete_race_date": str(merged.race_date.max()),
            "common_races": len(common),
        },
        "frozen_inputs": identity,
        "candidate": candidate,
        "baseline": previous,
        "comparison": comparisons,
        "adoption": adoption,
        "publication_authorized": False,
        "historical_card_publication_versions": "unverified",
    }
    write_json(output / "independent-final.json", report)
    if approved:
        model["adoption"] = adoption
        model["calibration_status"] = "fitted_on_separate_recent_period; verified_on_fixed_independent_period"
        model["provenance"]["periods"]["independent_final"] = {**report["period"], "status": "empirically_passed"}
        model["provenance"]["independent_final_evidence"] = {
            "report_sha256": digest_file(output / "independent-final.json"),
            **identity,
        }
        write_json(output / "approved-model.json", model)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.output, args.features, args.baseline)
    print(json.dumps({key: result[key] for key in ("status", "production_adopted", "common_races") if key in result}))


if __name__ == "__main__":
    main()
