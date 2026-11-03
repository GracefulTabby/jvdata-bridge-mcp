"""前日までの履歴から日単位で基準特徴量を構築する。"""

from __future__ import annotations

import argparse
import hashlib
import time
from bisect import bisect_left
from collections import defaultdict, deque
from datetime import date
from pathlib import Path
from typing import Any

from jvdata_bridge_mcp.ml._development import archive_directory, write_json
from jvdata_bridge_mcp.ml.features import (
    CARD_FIELDS,
    FEATURE_NAMES,
    FEATURE_VERSION,
    add_field_ranks,
    day,
    enrich_run,
    mean,
    number,
    runner_features,
    valid_run,
)


def build_dataset(source: Path, output: Path, *, feature_recipe: str = "reference") -> Path:
    """全レースを日単位で確定してから履歴へ追加し、同日リークを防ぐ。"""
    import numpy as np
    import pandas as pd

    output = archive_directory(output)
    runs = pd.read_parquet(source / "runs.parquet")
    runs = runs.sort_values(["race_date", "race_id", "horse_number"]).replace({np.nan: None})
    if runs.duplicated(["race_id", "horse_id"]).any():
        raise ValueError("duplicate race/horse rows; extraction must be unique")
    speed_frame = pd.read_parquet(source / "speed.parquet")
    speeds = {(r["race_id"], r["horse_id"]): r for r in speed_frame.to_dict("records")}
    del speed_frame
    train_frame = pd.read_parquet(
        source / "training.parquet",
        columns=[
            "horse_id",
            "training_date",
            "four_furlongs_seconds",
            "lap_200_0_seconds",
            "quality_status",
        ],
    )
    train_frame = train_frame.sort_values(["horse_id", "training_date"]).replace({np.nan: None})
    sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in train_frame.to_dict("records"):
        sessions[str(r["horse_id"])].append(r)
    del train_frame
    session_dates = {h: [day(s["training_date"]).toordinal() for s in rows] for h, rows in sessions.items()}
    horses: dict[str, list[dict[str, Any]]] = defaultdict(list)
    jockeys: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=50))
    trainers: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=50))
    paces: dict[tuple[str, str, float], deque[float]] = defaultdict(lambda: deque(maxlen=400))
    output_rows = []
    excluded: dict[str, int] = defaultdict(int)
    started = time.perf_counter()
    for day_index, (race_day, daily) in enumerate(runs.groupby("race_date", sort=False)):
        pending = []
        for race_id, race in daily.groupby("race_id", sort=False):
            records = race.to_dict("records")
            # Flat JRA only. Labels use complete official results; withdrawals are not outcomes.
            flat = str(records[0].get("track_code") or "")[:1] in {"1", "2"}
            active = [r for r in records if r.get("is_starter") is True]
            complete = len(active) >= 3 and all(
                r.get("is_complete_result") is True and r.get("is_final_result") is True for r in active
            )
            field = active if complete else records
            key = (
                str(records[0]["venue_code"]),
                str(records[0]["track_code"])[:1],
                number(records[0]["distance_meters"]),
            )
            features = []
            for r in field:
                horse = str(r["horse_id"])
                dates = session_dates.get(horse, [])
                current_day = day(race_day).toordinal()
                available_training = sessions.get(horse, [])[
                    bisect_left(dates, current_day - 180) : bisect_left(dates, current_day)
                ]
                features.append(
                    runner_features(
                        {k: v for k, v in r.items() if k in CARD_FIELDS},
                        list(reversed(horses[horse])),
                        list(jockeys[str(r["jockey_id"])]),
                        list(trainers[str(r["trainer_id"])]),
                        speeds.get((race_id, horse)),
                        available_training,
                        field_size=len(field),
                        pace_history=list(paces[key]),
                        feature_recipe=feature_recipe,
                    )
                )
            add_field_ranks(features)
            from jvdata_bridge_mcp.ml.features import smoothed

            prior_opponent = {
                str(r["horse_id"]): smoothed(
                    [past for past in reversed(horses[str(r["horse_id"])]) if valid_run(past)][:5],
                    "finish_quality",
                    strength=1.5,
                )
                for r in field
            }
            finishers = [r for r in records if valid_run(r)]
            clocks = [number(r.get("last_three_furlongs_seconds")) for r in finishers]
            clocks = [c for c in clocks if np.isfinite(c)]
            for r in records:
                run = enrich_run(r)
                if not valid_run(r):
                    run["finish_quality"] = float("nan")
                clock = number(r.get("last_three_furlongs_seconds"))
                run["closing_quality"] = (
                    (sum(c > clock for c in clocks) + 0.5 * (clocks.count(clock) - 1)) / (len(clocks) - 1)
                    if len(clocks) >= 2 and np.isfinite(clock)
                    else float("nan")
                )
                run["opponent_strength"] = mean([q for h, q in prior_opponent.items() if h != str(r["horse_id"])])
                pending.append(run)
            if not flat or not complete or not any(number(r.get("finish_position")) == 1 for r in active):
                excluded["nonflat_or_incomplete"] += 1
                continue
            if day(race_day) < date(2018, 1, 1):
                continue
            for r, f in zip(field, features, strict=True):
                finish = number(r.get("finish_position"), 99)
                if not r.get("is_finisher"):
                    finish = 99
                pace = number(r.get("first_three_furlongs_seconds"))
                mu, sd = f["course_pace_mean"], f["course_pace_std"]
                z = (mu - pace) / sd if sd > 0 and f["course_pace_count"] >= 30 else float("nan")
                early = next(
                    (
                        number(r.get(k))
                        for k in ("corner_1_position", "corner_2_position", "corner_3_position")
                        if number(r.get(k)) >= 1
                    ),
                    float("nan"),
                )
                row = {
                    **f,
                    "race_id": race_id,
                    "race_date": str(race_day),
                    "horse_id": r["horse_id"],
                    "horse_number": r["horse_number"],
                    "venue_code": r["venue_code"],
                    "track_code": r["track_code"],
                    "finish": finish,
                    "win": float(finish == 1),
                    "top3": float(finish <= 3),
                    "early_rank": early,
                    "fourth_rank": number(r.get("corner_4_position")),
                    "pace_seconds": pace,
                    "pace_z": z,
                    "pace_class": (2 if z >= 0.5 else 0 if z <= -0.5 else 1) if np.isfinite(z) else -1,
                    "has_dead_heat": bool(
                        number(r.get("dead_heat_count"), 0) > 1 or str(r.get("dead_heat_code")) == "1"
                    ),
                    "history_max_date": max((str(x["race_date"]) for x in horses[str(r["horse_id"])]), default=None),
                }
                output_rows.append(row)
        # All updates happen after the entire date, regardless of venue or race code.
        for r in pending:
            horses[str(r["horse_id"])].append(r)
            jockeys[str(r["jockey_id"])].appendleft(r)
            trainers[str(r["trainer_id"])].appendleft(r)
        for _, race in daily.groupby("race_id", sort=False):
            r = race.iloc[0]
            clock = number(r["first_three_furlongs_seconds"])
            if bool(r["is_final_result"]) and bool(r["is_complete_result"]) and 25 <= clock <= 50:
                paces[(str(r["venue_code"]), str(r["track_code"])[:1], number(r["distance_meters"]))].append(clock)
        if day_index % 100 == 0:
            print(f"features {race_day}: {len(output_rows)} rows, {time.perf_counter() - started:.1f}s", flush=True)
    del runs, sessions, session_dates, horses, jockeys, trainers, paces, speeds
    import gc

    gc.collect()
    frame = pd.DataFrame(output_rows)
    bad = frame.history_max_date.notna() & (frame.history_max_date >= frame.race_date)
    if bad.any():
        raise ValueError("history date leakage")
    dest = output / "features.parquet"
    frame.to_parquet(dest, index=False)
    write_json(
        output / "dataset.json",
        {
            "feature_version": FEATURE_VERSION,
            "feature_recipe": feature_recipe,
            "features": list(FEATURE_NAMES),
            "rows": len(frame),
            "races": int(frame.race_id.nunique()),
            "excluded": dict(excluded),
            "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
            "missing_rates": {k: float(frame[k].isna().mean()) for k in FEATURE_NAMES},
            "leakage_checks": {"unique_race_horse": True, "strict_prior_date": True, "target_market_excluded": True},
            "limitations": [
                "stored card versions and historical availability times are not reconstructed",
                "2010 history warmup; older lifetime runs unavailable",
                "evaluation conditional on actual starters",
                "development exposure must be recorded in the study protocol",
            ],
        },
    )
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_dataset(args.source, args.output)


if __name__ == "__main__":
    main()
