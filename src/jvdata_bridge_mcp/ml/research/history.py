"""レース前履歴の時計・位置・相手関係を導出する。"""

from pathlib import Path

import numpy as np
import pandas as pd


def enriched_runs(path: Path) -> pd.DataFrame:
    columns = [
        "race_id",
        "race_date",
        "horse_id",
        "horse_number",
        "venue_code",
        "track_code",
        "distance_meters",
        "starters",
        "finish_position",
        "time_difference_seconds",
        "last_three_furlongs_seconds",
        "corner_1_position",
        "corner_2_position",
        "corner_3_position",
        "corner_4_position",
        "is_final_result",
        "is_complete_result",
        "is_starter",
        "is_finisher",
    ]
    frame = (
        pd.read_parquet(path, columns=columns)
        .sort_values(["race_date", "race_id", "horse_number"])
        .reset_index(drop=True)
    )
    valid = frame[["is_final_result", "is_complete_result", "is_starter", "is_finisher"]].eq(True).all(axis=1)
    frame["valid"] = valid
    n = frame.starters.astype(float)
    finish = frame.finish_position.astype(float)
    frame["finish_quality"] = ((n - finish) / (n - 1)).where((n > 1) & (finish >= 1) & (finish <= n))
    margin = frame.time_difference_seconds.astype(float)
    frame["time_quality"] = np.exp(-margin.clip(lower=0))
    clocks = frame.last_three_furlongs_seconds.astype(float).where(valid)
    counts = clocks.groupby(frame.race_id, sort=False).transform("count")
    ranks = clocks.groupby(frame.race_id, sort=False).rank(method="average")
    frame["closing_quality"] = ((counts - ranks) / (counts - 1)).where(counts > 1)
    corners = frame[["corner_1_position", "corner_2_position", "corner_3_position"]].to_numpy(dtype=float)
    early = np.where(corners[:, 0] >= 1, corners[:, 0], np.where(corners[:, 1] >= 1, corners[:, 1], corners[:, 2]))
    early[early < 1] = np.nan
    fourth = frame.corner_4_position.astype(float).where(frame.corner_4_position >= 1).to_numpy()
    for stage, position in (("early", early), ("fourth", fourth)):
        frame[f"{stage}_frontness"] = np.where(
            (n > 1) & (position >= 1) & (position <= n), (n - position) / (n - 1), np.nan
        )
    frame["early_lead"] = np.where(np.isfinite(early), (early == 1).astype(float), np.nan)
    frame["early_top3"] = np.where(np.isfinite(early), (early <= 3).astype(float), np.nan)
    frame["rank_gain"] = early - fourth
    dates = pd.to_datetime(frame.race_date).to_numpy(dtype="datetime64[D]").astype(np.int64)
    frame["date_number"] = dates
    prior = np.full(len(frame), 0.5)
    qualities = frame.finish_quality.to_numpy(dtype=float)
    valid_values = valid.to_numpy()
    for indices in frame.groupby("horse_id", sort=False).indices.values():
        past = indices[valid_values[indices]]
        positions = np.searchsorted(dates[past], dates[indices], side="left")
        numerator, denominator = np.full(len(indices), 0.75), np.full(len(indices), 1.5)
        for lag in range(5):
            location = positions - lag - 1
            if not len(past):
                break
            values = qualities[past[np.maximum(location, 0)]]
            present = (location >= 0) & np.isfinite(values)
            weight = 0.72**lag
            numerator += np.where(present, values * weight, 0)
            denominator += present * weight
        prior[indices] = numerator / denominator
    active = frame.is_starter.eq(True)
    complete = (
        (~active | frame[["is_complete_result", "is_final_result"]].eq(True).all(axis=1))
        .groupby(frame.race_id, sort=False)
        .transform("all")
    )
    active_count = active.groupby(frame.race_id, sort=False).transform("sum")
    member = ~((active_count >= 3) & complete) | active
    values = pd.Series(prior).where(member)
    sums = values.groupby(frame.race_id, sort=False).transform("sum")
    counts = values.groupby(frame.race_id, sort=False).transform("count")
    frame["opponent_strength"] = (sums - np.where(member, prior, 0)) / (counts - member.astype(int)).replace(0, np.nan)
    return frame
