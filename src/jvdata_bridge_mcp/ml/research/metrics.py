"""レースを単位とする確率評価。順位評価だけで採用しない。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np


def calibration_curve(probability: Sequence[float], labels: Sequence[float]) -> list[dict[str, Any]]:
    p, y = np.asarray(probability), np.asarray(labels)
    rows = []
    for lo, hi in zip(np.linspace(0, 1, 11)[:-1], np.linspace(0, 1, 11)[1:], strict=True):
        mask = (p >= lo) & ((p <= hi) if hi == 1 else (p < hi))
        rows.append(
            {
                "lower": float(lo),
                "upper": float(hi),
                "count": int(mask.sum()),
                "mean_probability": float(p[mask].mean()) if mask.any() else None,
                "frequency": float(y[mask].mean()) if mask.any() else None,
            }
        )
    return rows


def probability_metrics(
    frame: Any, p: np.ndarray, *, stage_powers: Sequence[float] = (1, 1), joint: bool = True
) -> dict[str, Any]:
    from sklearn.metrics import ndcg_score

    if len(p) != len(frame) or not np.isfinite(p).all() or (p < 0).any():
        raise ValueError("invalid probabilities")
    results = []
    top2_predictions, top2_labels, top3_predictions, top3_labels = [], [], [], []
    for (_, race), indices in zip(
        frame.groupby("race_id", sort=False),
        np.split(np.arange(len(frame)), np.cumsum(frame.groupby("race_id", sort=False).size())[:-1]),
        strict=True,
    ):
        scores = p[indices]
        if abs(scores.sum() - 1) > 1e-8:
            raise ValueError("race probabilities do not sum to one")
        wins = race.win.to_numpy(dtype=float)
        wins = wins / wins.sum()
        order = np.argsort(-scores, kind="stable")
        finish = race.finish.to_numpy(dtype=float)
        relevance = np.maximum(0, 4 - finish)
        r = {
            "race_id": str(race.iloc[0].race_id),
            "log_loss": float(-np.sum(wins * np.log(np.maximum(scores, 1e-15)))),
            "brier": float(np.mean((scores - wins) ** 2)),
            "winner_top3": float(wins[order[:3]].sum()),
            "ndcg": float(ndcg_score([relevance], [scores], k=3)),
            "top3_log_loss": None,
            "top3_brier": None,
            "top2_log_loss": None,
            "top2_brier": None,
            "joint_log_loss": None,
            "quinella_log_loss": None,
            "trio_log_loss": None,
            "wide_brier": None,
        }
        if joint:
            numbers = list(range(len(race)))
            w2, w3 = scores ** stage_powers[0], scores ** stage_powers[1]
            pair = scores[:, None] * w2[None, :] / (w2.sum() - w2[:, None])
            np.fill_diagonal(pair, 0)
            denominator = w3.sum() - w3[:, None] - w3[None, :]
            cube = pair[:, :, None] * w3[None, None, :] / np.maximum(denominator[:, :, None], 1e-100)
            for i in numbers:
                cube[i, i, :] = 0
                cube[i, :, i] = 0
                cube[:, i, i] = 0
            if abs(cube.sum() - 1) > 1e-8:
                raise ValueError("joint probabilities do not sum to one")
            t3 = scores + pair.sum(axis=0) + cube.sum(axis=(0, 1))
            t2 = scores + pair.sum(axis=0)
            labels2 = (finish <= 2).astype(float)
            clipped2 = np.clip(t2, 1e-15, 1 - 1e-15)
            r["top2_log_loss"] = float(-np.mean(labels2 * np.log(clipped2) + (1 - labels2) * np.log1p(-clipped2)))
            r["top2_brier"] = float(np.mean((t2 - labels2) ** 2))
            top2_predictions.extend(t2.tolist())
            top2_labels.extend(labels2.tolist())
            labels = (finish <= 3).astype(float)
            clipped = np.clip(t3, 1e-15, 1 - 1e-15)
            r["top3_log_loss"] = float(-np.mean(labels * np.log(clipped) + (1 - labels) * np.log1p(-clipped)))
            r["top3_brier"] = float(np.mean((t3 - labels) ** 2))
            r["top3_recall"] = float(labels[order[:3]].sum() / max(1, labels.sum()))
            top3_predictions.extend(t3.tolist())
            top3_labels.extend(labels.tolist())
            classified = np.argsort(finish, kind="stable")[:3]
            strict = len(set(finish[classified])) == 3 and all(finish[classified] == [1, 2, 3])
            if strict:
                first, second, third = (int(i) for i in classified)
                r["joint_log_loss"] = -math.log(max(1e-15, cube[first, second, third]))
                r["quinella_log_loss"] = -math.log(max(1e-15, pair[first, second] + pair[second, first]))
                idx = [first, second, third]
                r["trio_log_loss"] = -math.log(max(1e-15, cube[np.ix_(idx, idx, idx)].sum()))
                both = cube.sum(axis=0) + cube.sum(axis=1) + cube.sum(axis=2)
                both = both + both.T
                ix = np.triu_indices(len(race), k=1)
                wide_labels = np.outer(finish <= 3, finish <= 3)[ix].astype(float)
                r["wide_brier"] = float(np.mean((both[ix] - wide_labels) ** 2))
        results.append(r)
    keys = [key for key in results[0] if key != "race_id"]
    return {
        "races": len(results),
        "runners": len(frame),
        **{
            key: float(np.mean(vals)) if (vals := [r[key] for r in results if r.get(key) is not None]) else None
            for key in keys
        },
        "win_calibration": calibration_curve(
            p, frame.win.to_numpy() / frame.groupby("race_id", sort=False).win.transform("sum").to_numpy()
        ),
        "top3_calibration": calibration_curve(top3_predictions, top3_labels) if joint else [],
        "top2_calibration": calibration_curve(top2_predictions, top2_labels) if joint else [],
        "per_race": results,
    }


def paired_day_interval(
    candidate: dict[str, Any], baseline: dict[str, Any], key: str, race_dates: dict[str, str], *, draws: int = 2000
) -> dict[str, Any]:
    """同日の依存を保ち、日単位で再標本化したレース平均差の区間。"""
    from collections import defaultdict

    base = {row["race_id"]: row.get(key) for row in baseline["per_race"]}
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in candidate["per_race"]:
        race = row["race_id"]
        if row.get(key) is not None and base.get(race) is not None:
            grouped[race_dates[race]].append(row[key] - base[race])
    if not grouped:
        return {"days": 0, "races": 0, "mean_delta": None, "interval_95": None}
    totals = np.array([sum(values) for values in grouped.values()])
    counts = np.array([len(values) for values in grouped.values()])
    if len(totals) < 2:
        interval = None
    else:
        indices = np.random.default_rng(617).integers(0, len(totals), size=(draws, len(totals)))
        means = totals[indices].sum(axis=1) / counts[indices].sum(axis=1)
        interval = [float(x) for x in np.quantile(means, [0.025, 0.975])]
    return {
        "days": len(totals),
        "races": int(counts.sum()),
        "mean_delta": float(totals.sum() / counts.sum()),
        "interval_95": interval,
        "resampling_unit": "race_date",
    }


def pace_metrics(
    actual: np.ndarray, probability: np.ndarray, seconds: np.ndarray, predicted: np.ndarray
) -> dict[str, Any]:
    from sklearn.metrics import confusion_matrix, f1_score, log_loss, recall_score

    labels = probability.argmax(axis=1)
    return {
        "count": len(actual),
        "accuracy": float(np.mean(actual == labels)),
        "macro_f1": float(f1_score(actual, labels, labels=[0, 1, 2], average="macro", zero_division=0)),
        "recall": [float(x) for x in recall_score(actual, labels, labels=[0, 1, 2], average=None, zero_division=0)],
        "log_loss": float(log_loss(actual, probability, labels=[0, 1, 2])),
        "mae_seconds": float(np.mean(np.abs(seconds - predicted))),
        "confusion_matrix": confusion_matrix(actual, labels, labels=[0, 1, 2]).tolist(),
        "predicted_class_counts": np.bincount(labels, minlength=3).tolist(),
        "actual_class_counts": np.bincount(actual, minlength=3).tolist(),
    }
