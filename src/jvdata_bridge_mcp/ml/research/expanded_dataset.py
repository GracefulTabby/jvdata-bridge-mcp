"""公式条件・過去時計・調教・出走構成を含む特徴量の構築。"""

from __future__ import annotations

import argparse
import gc
import time
from collections import defaultdict, deque
from itertools import groupby
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from jvdata_bridge_mcp.ml._development import archive_directory, digest_file, write_json
from jvdata_bridge_mcp.ml.expanded_features import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    add_field_extensions,
    clock_context,
    official_class,
    runner_extensions,
)
from jvdata_bridge_mcp.ml.features import COMPONENTS, number, runner_features
from jvdata_bridge_mcp.ml.features import FEATURE_NAMES as BASE_NAMES
from jvdata_bridge_mcp.ml.research.history import enriched_runs


def normalized_training(extract: Path) -> pd.DataFrame:
    frame = pd.read_parquet(extract / "training.parquet")
    reference = pd.read_parquet(extract / "training-reference.parquet")
    keys = ["training_type", "training_center_code", "course_code"]
    for part in (frame, reference):
        part["training_date"] = pd.to_datetime(part.training_date)
        for name in keys:
            part[name] = part[name].fillna("").astype(str)
    statistics = []
    columns = ["n4", "sum4", "sq4", "n1", "sum1", "sq1"]
    for _, group in reference.groupby(keys, sort=False, observed=True):
        group = group.sort_values("training_date").set_index("training_date")
        prior = group[columns].rolling("365D", closed="left").sum().fillna(0)
        for name in keys:
            prior[name] = group[name]
        statistics.append(prior.reset_index())
    reference = pd.concat(statistics, ignore_index=True)
    frame = frame.merge(reference, on=["training_date", *keys], how="left", validate="many_to_one", sort=False)
    for suffix, column, bounds in (("4", "four_furlongs_seconds", (35, 80)), ("1", "lap_200_0_seconds", (8, 25))):
        n, total, square = (frame[f"{prefix}{suffix}"].to_numpy(dtype=float) for prefix in ("n", "sum", "sq"))
        center = total / np.maximum(n, 1)
        deviation = np.sqrt(np.maximum(0, (square - total * center) / np.maximum(n - 1, 1)))
        values = frame[column].to_numpy(dtype=float)
        normalized = np.clip((center - values) / np.maximum(deviation, 0.2), -10, 10)
        normalized[(n < 30) | (values < bounds[0]) | (values > bounds[1])] = np.nan
        frame["normalized_4f" if suffix == "4" else "normalized_last1f"] = normalized
    frame = frame.drop(columns=columns)
    for name in ["horse_id", *keys, "quality_status"]:
        frame[name] = frame[name].astype("category")
    return frame.sort_values(["horse_id", "training_date"], kind="stable").reset_index(drop=True)


def add_class_and_clocks(frame: pd.DataFrame, extract: Path) -> tuple[pd.DataFrame, dict[str, list[float]]]:
    classes = pd.read_parquet(extract / "classes.parquet")
    pivot = classes.pivot(index="race_id", columns="age_key", values="condition_code")
    codes = pivot.where(pivot.notna(), None).to_dict("index")
    frame["official_class_rank"] = [
        official_class(codes.get(race), age) for race, age in zip(frame.race_id, frame.horse_age, strict=True)
    ]
    race = frame.groupby("race_id", sort=False).first().reset_index()
    winners = (
        frame[frame.valid & frame.finish_position.eq(1)]
        .groupby("race_id", sort=False)[["elapsed_seconds", "last_three_furlongs_seconds"]]
        .mean()
        .rename(columns={"elapsed_seconds": "winner_seconds", "last_three_furlongs_seconds": "winner_closing"})
    )
    race = race.merge(winners, on="race_id", how="left", validate="one_to_one")
    race["ground"] = np.where(race.track_code.str[:1].eq("1"), race.turf_condition_code, race.dirt_condition_code)
    race["pace_seconds"] = race.first_three_furlongs_seconds
    contexts = {}
    pace_contexts = {}
    context_columns = ["race_id", "race_date", "ground", "winner_seconds", "winner_closing", "pace_seconds"]
    for group_index, (_, group) in enumerate(
        race.groupby(["venue_code", "track_code", "distance_meters"], sort=False)
    ):
        prior: deque[dict[str, Any]] = deque(maxlen=400)
        for race_date, daily in groupby(
            group.sort_values(["race_date", "race_id"])[context_columns].to_dict("records"),
            key=lambda row: row["race_date"],
        ):
            records = list(daily)
            for row in records:
                contexts[row["race_id"]] = clock_context(list(prior), race_date, row["ground"])
            for row in records:
                if np.isfinite(number(row["winner_seconds"])):
                    prior.append(row)
        if group_index % 50 == 0:
            print("clock contexts", group_index, flush=True)
    race["surface"] = race.track_code.str[:1]
    older = race[race.race_date.astype(str) < "2018-01-01"]
    for _, group in older.groupby(["venue_code", "surface", "distance_meters"], sort=False):
        baseline: deque[float] = deque(maxlen=400)
        columns = ["race_id", "race_date", "pace_seconds", "is_complete_result", "is_final_result"]
        for _, daily in groupby(
            group.sort_values(["race_date", "race_id"])[columns].to_dict("records"), key=lambda row: row["race_date"]
        ):
            records = list(daily)
            for row in records:
                pace_contexts[row["race_id"]] = list(baseline)
            for row in records:
                pace = number(row["pace_seconds"])
                if (
                    row["is_complete_result"] is True
                    and row["is_final_result"] is True
                    and np.isfinite(pace)
                    and 25 <= pace <= 50
                ):
                    baseline.append(float(pace))
    for output, field, prefix in (
        ("clock_residual", "elapsed_seconds", "clock"),
        ("closing_residual", "last_three_furlongs_seconds", "closing"),
        ("historical_pace_z", "first_three_furlongs_seconds", "pace"),
    ):
        center = frame.race_id.map({race: values[f"{prefix}_mean"] for race, values in contexts.items()}).to_numpy(
            dtype=float
        )
        scale = frame.race_id.map({race: values[f"{prefix}_std"] for race, values in contexts.items()}).to_numpy(
            dtype=float
        )
        frame[output] = np.clip((center - frame[field].to_numpy(dtype=float)) / scale, -10, 10)
    frame["clock_reference_count"] = frame.race_id.map(
        {race: values["clock_count"] for race, values in contexts.items()}
    )
    return frame, pace_contexts


def people_values(frame: pd.DataFrame, person: str) -> np.ndarray:
    is_jockey = person == "jockey"
    limit, years, strength, decay = (30, 730, 8, 0.985) if is_jockey else (50, 1095, 12, 0.99)
    values = np.full(len(frame), 0.5)
    histories: dict[str, deque[tuple[int, float]]] = defaultdict(lambda: deque(maxlen=50))
    for _, daily in frame.groupby("race_date", sort=False):
        pending = []
        for index, person_id, date_number, quality, valid in daily[
            [f"{person}_id", "date_number", "finish_quality", "valid"]
        ].itertuples():
            prior = [(day, q) for day, q in histories[str(person_id)] if date_number - day <= years][:limit]
            numerator, denominator = 0.5 * strength, float(strength)
            for lag, (_, q) in enumerate(prior):
                if np.isfinite(q):
                    weight = decay**lag
                    numerator += q * weight
                    denominator += weight
            values[index] = numerator / denominator
            pending.append((str(person_id), int(date_number), float(quality) if valid else np.nan))
        for person_id, date_number, quality in pending:
            histories[person_id].appendleft((date_number, quality))
    return values


def build(source: Path, extract: Path, reference: Path, output: Path) -> Path:
    output = archive_directory(output)
    destination = output / "features.parquet"
    if destination.exists():
        raise ValueError("expanded dataset will not be overwritten")
    started = time.perf_counter()
    frame = (
        pd.read_parquet(source / "runs.parquet")
        .sort_values(["race_date", "race_id", "horse_number"])
        .reset_index(drop=True)
    )
    derived = enriched_runs(source / "runs.parquet")
    assert frame[["race_id", "horse_id"]].equals(derived[["race_id", "horse_id"]])
    for name in derived:
        if name not in frame:
            frame[name] = derived[name].to_numpy()
    del derived
    frame, pace_contexts = add_class_and_clocks(frame, extract)
    print("expanded histories", len(frame), round(time.perf_counter() - started, 1), flush=True)
    training = normalized_training(extract)
    print("normalized training", len(training), round(time.perf_counter() - started, 1), flush=True)
    active = frame.is_starter.eq(True)
    size = active.groupby(frame.race_id, sort=False).transform("sum")
    complete = (
        (frame.is_final_result.eq(True) & frame.is_complete_result.eq(True) | ~active)
        .groupby(frame.race_id, sort=False)
        .transform("all")
    )
    winner = (active & frame.finish_position.eq(1)).groupby(frame.race_id, sort=False).transform("any")
    eligible = (
        active
        & complete
        & winner
        & size.between(3, 18)
        & frame.track_code.str[:1].isin(["1", "2"])
        & (frame.race_date.astype(str) >= "2012-01-01")
    )
    target_indices = np.flatnonzero(eligible.to_numpy())
    positions = np.full(len(frame), -1, dtype=int)
    positions[target_indices] = np.arange(len(target_indices))
    values = {name: np.full(len(target_indices), np.nan) for name in FEATURE_NAMES}
    target = (
        frame.loc[
            eligible,
            [
                "race_id",
                "horse_id",
                "horse_number",
                "race_date",
                "venue_code",
                "track_code",
                "finish_position",
                "is_finisher",
                "has_dead_heat",
            ]
            if "has_dead_heat" in frame
            else [
                "race_id",
                "horse_id",
                "horse_number",
                "race_date",
                "venue_code",
                "track_code",
                "finish_position",
                "is_finisher",
            ],
        ]
        .copy()
        .reset_index(drop=True)
    )
    base = pd.read_parquet(reference / "features.parquet")
    keys = pd.MultiIndex.from_frame(target[["race_id", "horse_id"]])
    base_index = pd.MultiIndex.from_frame(base[["race_id", "horse_id"]])
    mapped = base_index.get_indexer(keys)
    existing = mapped >= 0
    if not np.array_equal(
        np.flatnonzero(existing), np.flatnonzero(target.race_date.astype(str).to_numpy() >= "2018-01-01")
    ):
        raise ValueError("reference roster differs from extended dataset")
    for name in BASE_NAMES:
        values[name][existing] = base[name].to_numpy()[mapped[existing]]
    old_frame = frame[frame.race_date.astype(str) < "2018-01-01"]
    jockey = people_values(old_frame, "jockey")
    trainer = people_values(old_frame, "trainer")
    training_groups = training.groupby("horse_id", sort=False, observed=True).indices
    training_days = training.training_date.to_numpy(dtype="datetime64[D]").astype(np.int64)
    training_columns = {name: training[name].to_numpy() for name in training if name != "horse_id"}
    dates = frame.date_number.to_numpy()
    fields = size.to_numpy()
    history_columns = [
        name for name in frame if name not in {"scheduled_start_at", "final_win_odds", "final_win_popularity"}
    ]
    history_max = np.full(len(target), None, dtype=object)
    for horse_index, (horse, indices) in enumerate(frame.groupby("horse_id", sort=False).indices.items()):
        selected = indices[positions[indices] >= 0]
        if not len(selected):
            continue
        records = frame.iloc[indices][history_columns].replace({np.nan: None}).to_dict("records")
        training_indices = training_groups.get(horse, np.empty(0, dtype=int))
        sessions = [{name: array[i] for name, array in training_columns.items()} for i in training_indices]
        for index in selected:
            location = positions[index]
            end = int(np.searchsorted(dates[indices], dates[index], side="left"))
            history = list(reversed(records[:end]))
            low, high = np.searchsorted(
                training_days[training_indices], [dates[index] - 180, dates[index]], side="left"
            )
            recent_training = sessions[low:high]
            card = records[int(np.searchsorted(indices, index))]
            if not existing[location]:
                row = runner_features(
                    card,
                    history,
                    [],
                    [],
                    None,
                    recent_training,
                    field_size=int(fields[index]),
                    pace_history=pace_contexts[card["race_id"]],
                )
                row["jockey_form_raw"], row["trainer_form_raw"] = jockey[index], trainer[index]
                for name in BASE_NAMES:
                    values[name][location] = row.get(name, np.nan)
            extension = runner_extensions(card, history, recent_training)
            for name, value in extension.items():
                values[name][location] = value
            history_max[location] = str(history[0]["race_date"]) if history else None
        if horse_index % 5000 == 0:
            print("expanded horses", horse_index, round(time.perf_counter() - started, 1), flush=True)
    for name, array in values.items():
        target[name] = array
    for name in COMPONENTS:
        column = target[f"{name}_raw"]
        counts = column.groupby(target.race_id, sort=False).transform("count")
        ranks = column.groupby(target.race_id, sort=False).rank(method="min")
        target[f"{name}_score"] = (ranks - 1) / (counts - 1).clip(lower=1)
    for indices in target.groupby("race_id", sort=False).indices.values():
        rows = target.iloc[indices][list(FEATURE_NAMES)].to_dict("records")
        add_field_extensions(rows)
        from jvdata_bridge_mcp.ml.expanded_features import EXTENSION_GROUPS

        for name in EXTENSION_GROUPS["field"]:
            target.loc[indices, name] = [row[name] for row in rows]
    target["race_date"] = target.race_date.astype(str)
    target["finish"] = np.where(target.is_finisher.eq(True), target.finish_position.fillna(99), 99)
    target["win"] = target.finish.eq(1).astype(float)
    target["top3"] = target.finish.le(3).astype(float)
    target["history_max_date"] = history_max
    target["has_dead_heat"] = frame.loc[eligible, "dead_heat_count"].fillna(0).to_numpy() > 1
    if (target.history_max_date.notna() & (target.history_max_date >= target.race_date)).any():
        raise ValueError("extended dataset date leakage")
    reference_values = target.loc[existing].set_index(["race_id", "horse_id"])[list(BASE_NAMES)].sort_index()
    expected = base.set_index(["race_id", "horse_id"])[list(BASE_NAMES)].sort_index()
    if not np.array_equal(np.isnan(reference_values), np.isnan(expected)):
        raise ValueError("base feature missingness changed")
    error = float(np.nanmax(np.abs(reference_values.to_numpy() - expected.to_numpy())))
    if error > 1e-10:
        raise ValueError(f"base feature parity mismatch: {error}")
    del frame, training, training_columns, records, sessions, base, values
    gc.collect()
    target = target.sort_values(["race_date", "race_id", "horse_number"]).reset_index(drop=True)
    target.to_parquet(destination, index=False)
    write_json(
        output / "dataset.json",
        {
            "feature_version": FEATURE_VERSION,
            "features": list(FEATURE_NAMES),
            "rows": len(target),
            "races": int(target.race_id.nunique()),
            "by_year": target.groupby(target.race_date.str[:4]).race_id.nunique().to_dict(),
            "sha256": digest_file(destination),
            "reference_sha256": digest_file(reference / "features.parquet"),
            "base_parity_max_error": error,
            "base_parity_rows": int(existing.sum()),
            "missing_rates": {name: float(target[name].isna().mean()) for name in FEATURE_NAMES},
            "strict_prior_date": True,
            "history_start": "2010-01-01",
            "label_start": "2012-01-01",
            "training_reference": "same type/center/course, prior 365 calendar days; target/session day excluded",
            "clock_reference": "same venue/track/distance, prior 400 races; matching past ground when >=20; preceding dates only",
            "limits": [
                "stored card publication versions unverified",
                "speed missing before 2018",
                "first two years reserved for history warmup",
            ],
        },
    )
    print("expanded dataset saved", len(target), round(time.perf_counter() - started, 1), flush=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "extract", "reference", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.extract, args.reference, args.output)


if __name__ == "__main__":
    main()
