"""過去の固定カードを使う開発検査。ライブの過去予想APIや当時の配信時刻再現ではない。"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from jvdata_bridge_mcp.config import JvDataSettings
from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.ml._development import archive_directory, write_json
from jvdata_bridge_mcp.ml.expanded_features import EXTENSION_GROUPS, FEATURE_NAMES
from jvdata_bridge_mcp.ml.expanded_live import augment
from jvdata_bridge_mcp.ml.features import FEATURE_NAMES as BASE_NAMES
from jvdata_bridge_mcp.ml.features import add_field_ranks, enrich_run, runner_features, valid_run
from jvdata_bridge_mcp.ml.live import HISTORY_SQL, OPPONENT_SQL, TRAINING_SQL
from jvdata_bridge_mcp.tools._runtime import fetch_rows


async def audit(source: Path, output: Path) -> None:
    output = archive_directory(output)
    frame = pd.read_parquet(output / "data/features.parquet")
    snapshot = pd.read_parquet(source / "runs.parquet")
    known = frame.clock_residual_mean.notna().groupby(frame.race_id, sort=False).transform("mean")
    protocol = json.loads((output / "protocol.json").read_bytes())
    latest = protocol["outer_development_years"][-1]
    eligible = frame[(frame.race_date >= f"{latest}-01-01") & (frame.field_size >= 14) & (known >= 0.8)]
    race_rows = eligible.groupby("race_id", sort=False).first()
    races = race_rows.groupby(race_rows.track_code.str[:1], sort=True).head(1).index.tolist()
    database = JvDataDatabase(JvDataSettings.from_env())
    errors = {name: 0.0 for name in FEATURE_NAMES}
    retained = {
        "jockey_form_raw",
        "trainer_form_raw",
        "speed_quality_raw",
        "speed_coverage",
        "speed_missing",
        "course_pace_mean",
        "course_pace_std",
        "course_pace_count",
    }
    base_errors = {name: 0.0 for name in BASE_NAMES if name not in retained}
    times = []
    try:
        for race_id in races:
            expected = frame[frame.race_id == race_id].sort_values("horse_number")
            cards = (
                snapshot[(snapshot.race_id == race_id) & snapshot.horse_id.isin(expected.horse_id)]
                .sort_values("horse_number")
                .to_dict("records")
            )
            params = {"horses": [card["horse_id"] for card in cards], "cutoff": str(cards[0]["race_date"])}
            began = time.perf_counter()
            history_rows = await fetch_rows(
                database, HISTORY_SQL, params, statement_timeout_seconds=180, disable_jit=True
            )
            histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in history_rows:
                run = enrich_run(row)
                if not valid_run(run):
                    run["finish_quality"] = math.nan
                histories[str(run["horse_id"])].append(run)
            past_ids = sorted(
                {run["race_id"] for history in histories.values() for run in [r for r in history if valid_run(r)][:5]}
            )
            opponents = await fetch_rows(
                database, OPPONENT_SQL, {"past_races": past_ids}, statement_timeout_seconds=180, disable_jit=True
            )
            strengths = {(row["race_id"], row["horse_id"]): row["opponent_strength"] for row in opponents}
            for history in histories.values():
                for row in history:
                    row["opponent_strength"] = strengths.get((row["race_id"], row["horse_id"]))
            sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in await fetch_rows(
                database, TRAINING_SQL, params, statement_timeout_seconds=180, disable_jit=True
            ):
                sessions[str(row["horse_id"])].append(row)
            input_seconds = time.perf_counter() - began
            recomputed = []
            for card, (_, target) in zip(cards, expected.iterrows(), strict=True):
                row = runner_features(
                    card,
                    histories[str(card["horse_id"])],
                    [],
                    [],
                    None,
                    sessions.get(str(card["horse_id"]), []),
                    field_size=len(cards),
                    pace_history=[],
                )
                row.update({name: target[name] for name in retained})
                recomputed.append(row)
            add_field_ranks(recomputed)
            for row, (_, target) in zip(recomputed, expected.iterrows(), strict=True):
                for name in base_errors:
                    if math.isfinite(row[name]) != math.isfinite(target[name]):
                        raise ValueError(f"base live/offline missingness differs: {name}")
                    if math.isfinite(row[name]):
                        base_errors[name] = max(base_errors[name], abs(row[name] - target[name]))
            values = expected[list(FEATURE_NAMES)].to_dict("records")
            began = time.perf_counter()
            provenance = await augment(database, cards, values, histories, sessions)
            extension_seconds = time.perf_counter() - began
            for actual, (_, target) in zip(values, expected.iterrows(), strict=True):
                for name in FEATURE_NAMES:
                    left, right = actual[name], target[name]
                    if math.isfinite(left) != math.isfinite(right):
                        raise ValueError(f"live/offline missingness differs: {name}")
                    if math.isfinite(left):
                        errors[name] = max(errors[name], abs(left - right))
            times.append(
                {
                    "runners": len(cards),
                    "prior_evidence_fetch_seconds": input_seconds,
                    "extension_seconds": extension_seconds,
                    "provenance": provenance,
                }
            )
    finally:
        await database.dispose()
    result = {
        "passed": max([*errors.values(), *base_errors.values()]) < 1e-8,
        "races": len(races),
        "errors": errors,
        "maximum_error": max(errors.values()),
        "base_recomputation_errors": base_errors,
        "retained_base_inputs": sorted(retained),
        "timings": times,
        "scope": "fixed development card; extension and prior horse evidence only; base feature queries and MCP serialization excluded",
        "post_development_labels_read": False,
    }
    write_json(output / "extended-live-parity.json", result)
    if not result["passed"]:
        raise ValueError(f"live/offline extension mismatch: {result['maximum_error']}")
    groups = {
        name: {feature: float(frame[feature].isna().mean()) for feature in fields}
        for name, fields in EXTENSION_GROUPS.items()
    }
    classes = pd.read_parquet(output / "extended/classes.parquet")
    write_json(
        output / "data-quality.json",
        {
            "races": int(frame.race_id.nunique()),
            "runners": len(frame),
            "by_year": frame.groupby(frame.race_date.str[:4]).race_id.nunique().to_dict(),
            "class_conflicting_age_fields": int(classes.conflict.sum()),
            "extension_missing_rates": groups,
            "official_class_missing_by_year": frame.official_class_rank.isna()
            .groupby(frame.race_date.str[:4])
            .mean()
            .to_dict(),
            "speed_missing_by_year": frame.speed_missing.groupby(frame.race_date.str[:4]).mean().to_dict(),
            "strict_prior_date": bool(
                not (frame.history_max_date.notna() & (frame.history_max_date >= frame.race_date)).any()
            ),
            "base_reference_parity": json.loads((output / "data/dataset.json").read_bytes())["base_parity_max_error"],
        },
    )
    print(json.dumps({"passed": True, "races": len(races), "maximum_error": max(errors.values()), "timings": times}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(audit(args.source, args.output))


if __name__ == "__main__":
    main()
