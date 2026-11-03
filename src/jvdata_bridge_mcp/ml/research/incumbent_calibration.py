"""同梱線形係数に対する較正の対照実験。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from jvdata_bridge_mcp.ml._development import archive_directory, digest_file, write_json
from jvdata_bridge_mcp.ml.research.calibration import (
    CALIBRATORS,
    calibrated_probabilities,
    fit_calibration,
    winner_scores,
)
from jvdata_bridge_mcp.ml.research.common import calibrate_stages, fold_ranges, subset, validate_partitions
from jvdata_bridge_mcp.ml.research.evaluation import common_rosters
from jvdata_bridge_mcp.ml.research.metrics import paired_day_interval, probability_metrics


def run(study: Path) -> dict[str, Any]:
    study = archive_directory(study)
    reference = study / "reference"
    output = archive_directory(study / "incumbent-calibration")
    protocol_path = study / "protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    ranges = fold_ranges(protocol["outer_development_years"][-1])
    intervals = {
        "initial_calibration": ranges["provisional_calibration"],
        "selection": ranges["selection"],
        "refreshed_calibration": ranges["final_calibration"],
        "development_assessment": (ranges["assessment"][0], protocol["development_end_exclusive"]),
    }
    frame = pd.read_parquet(reference / "features.parquet")
    old = pd.read_parquet(reference / "exact-baseline.parquet")
    common = common_rosters(frame, old, "horse_id")
    frame = (
        frame[frame.race_id.isin(common)]
        .merge(
            old[["race_id", "horse_id", "utility", "win_probability"]],
            on=["race_id", "horse_id"],
            validate="one_to_one",
        )
        .sort_values(["race_date", "race_id", "horse_number"])
        .reset_index(drop=True)
    )
    parts = {name: subset(frame, interval) for name, interval in intervals.items()}
    validate_partitions(parts)
    selected_scores = {}
    for kind in CALIBRATORS:
        calibration = fit_calibration(
            parts["initial_calibration"].utility.to_numpy(), parts["initial_calibration"], kind
        )
        p = calibrated_probabilities(parts["selection"].utility.to_numpy(), parts["selection"], calibration)
        selected_scores[kind] = {**winner_scores(parts["selection"], p), "fitted": calibration}
    admissible = [
        kind for kind, score in selected_scores.items() if score["brier"] <= selected_scores["identity"]["brier"]
    ]
    chosen = min(admissible, key=lambda kind: selected_scores[kind]["log_loss"])
    cal = parts["refreshed_calibration"]
    calibration = fit_calibration(cal.utility.to_numpy(), cal, chosen)
    powers = calibrate_stages(calibrated_probabilities(cal.utility.to_numpy(), cal, calibration), cal)
    assessment = parts["development_assessment"]
    p = calibrated_probabilities(assessment.utility.to_numpy(), assessment, calibration)
    corrected = probability_metrics(assessment, p, stage_powers=powers)
    winner_calibration_only = probability_metrics(assessment, p)
    unchanged = probability_metrics(assessment, assessment.win_probability.to_numpy())
    dates = dict(zip(assessment.race_id, assessment.race_date, strict=True))
    result = {
        "protocol_sha256": digest_file(protocol_path),
        "coefficient_update": False,
        "selection": selected_scores,
        "chosen_calibration": chosen,
        "refreshed_calibration": calibration,
        "stage_powers": powers,
        "unchanged": unchanged,
        "winner_calibration_only": winner_calibration_only,
        "winner_and_stage_calibration": corrected,
        "difference": {
            key: paired_day_interval(corrected, unchanged, key, dates)
            for key in ("log_loss", "brier", "top3_log_loss", "joint_log_loss")
        },
        "periods": {
            name: {
                "start": str(part.race_date.min()),
                "end": str(part.race_date.max()),
                "races": int(part.race_id.nunique()),
            }
            for name, part in parts.items()
        },
        "production_adopted": False,
        "independent_final": False,
    }
    assessment[["race_id", "race_date", "horse_id", "horse_number", "win", "finish"]].assign(
        corrected_probability=p, unchanged_probability=assessment.win_probability.to_numpy()
    ).to_parquet(output / "incumbent-calibration-scores.parquet", index=False)
    write_json(output / "incumbent-calibration.json", result)
    print(
        "incumbent calibration",
        chosen,
        {key: value["mean_delta"] for key, value in result["difference"].items()},
        flush=True,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    args = parser.parse_args()
    run(args.study)


if __name__ == "__main__":
    main()
