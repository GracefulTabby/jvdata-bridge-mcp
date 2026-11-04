"""共通の全頭ロスター・条件別評価・凍結モデル採点。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from jvdata_bridge_mcp.ml.inference import predict_probabilities, validate_model
from jvdata_bridge_mcp.ml.research.metrics import calibration_curve, paired_day_interval

SCORES = (
    "log_loss",
    "brier",
    "winner_top3",
    "ndcg",
    "top2_log_loss",
    "top2_brier",
    "top3_log_loss",
    "top3_brier",
    "joint_log_loss",
    "quinella_log_loss",
    "trio_log_loss",
    "wide_brier",
)


def common_rosters(frame: pd.DataFrame, other: pd.DataFrame, horse_key: str) -> set[str]:
    """線形モデルSQLと市場の比較は全頭ロスターの一致を必要とする。"""
    keys = ["race_id", horse_key]
    if frame.duplicated(keys).any() or other.duplicated(keys).any():
        raise ValueError("duplicate race/horse predictions")
    left = frame.groupby("race_id", sort=False)[horse_key].agg(set)
    right = other.groupby("race_id", sort=False)[horse_key].agg(set)
    return {race for race, horses in left.items() if race in right and horses == right[race]}


def monthly_comparisons(comparison: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    """既存のレース別スコアを月別に再集計し、短期間の悪化も隠さない。"""
    lookup = {
        name: {row["race_id"]: row for row in comparison[name]["per_race"]} for name in ("candidate", "baseline")
    }
    common = set(lookup["candidate"]) & set(lookup["baseline"])
    frame = frame.loc[frame.race_id.isin(common), :]
    dates = dict(zip(frame.race_id, frame.race_date, strict=True))
    result = {}
    for month, part in frame.groupby(frame.race_date.str[:7], sort=True):
        entry = {}
        for name in lookup:
            rows = [lookup[name][race] for race in part.race_id.unique()]
            entry[name] = {
                "races": len(rows),
                "runners": len(part),
                **{
                    key: float(np.mean(values))
                    if (values := [row[key] for row in rows if row[key] is not None])
                    else None
                    for key in SCORES
                },
                "per_race": rows,
            }
        entry["candidate_minus_baseline"] = {
            key: paired_day_interval(entry["candidate"], entry["baseline"], key, dates)
            for key in ("log_loss", "brier", "top3_log_loss", "joint_log_loss")
        }
        result[str(month)] = entry
    return result


def conditions(scores: pd.DataFrame, features: pd.DataFrame, metrics: dict[str, Any]) -> dict[str, Any]:
    """レース単位の層別。欠測馬や新馬だけを抜き出して勝率を再正規化しない。"""
    frame = scores.merge(
        features[["race_id", "horse_id", "distance", "grade", "history_count", "speed_missing"]],
        on=["race_id", "horse_id"],
        validate="one_to_one",
    )
    races = frame.groupby("race_id", sort=False).first()
    layers = {
        "month": races.race_date.str[:7],
        "venue": races.venue_code,
        "surface": races.track_code.str[:1],
        "distance": pd.cut(races.distance, [0, 1400, 1800, 2400, 5000], labels=["short", "mile", "middle", "long"]),
        "field_size": pd.cut(races.field_size, [0, 10, 14, 18], labels=["small", "medium", "large"]),
        "grade": races.grade,
        "any_speed_missing": frame.groupby("race_id", sort=False).speed_missing.max(),
        "any_short_history": frame.groupby("race_id", sort=False).history_count.min() < 3,
    }
    per_race = pd.DataFrame(metrics["per_race"]).set_index("race_id")
    result = {}
    for name, series in layers.items():
        for value in series.dropna().unique():
            ids = series.index[series == value]
            part = per_race.loc[ids]
            mask = frame.race_id.isin(ids)
            labels = frame.loc[mask, "win"] / frame.loc[mask].groupby("race_id", sort=False).win.transform("sum")
            result[f"{name}={value}"] = {
                "races": len(part),
                **{key: float(part[key].mean()) if part[key].notna().any() else None for key in SCORES},
                "win_calibration": calibration_curve(frame.loc[mask, "refreshed_fit"], labels),
            }
    return result


def score_frozen(model: dict[str, Any], frame: Any) -> np.ndarray:
    validate_model(model)
    probabilities = np.empty(len(frame))
    for indices in frame.groupby("race_id", sort=False).indices.values():
        probabilities[indices] = predict_probabilities(model, frame.iloc[indices].to_dict("records"))
    return probabilities
