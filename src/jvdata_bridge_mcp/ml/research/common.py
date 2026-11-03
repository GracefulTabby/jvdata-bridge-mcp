"""レース単位の時系列分割、較正、モデル保存。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import logsumexp

from jvdata_bridge_mcp.ml.features import FEATURE_NAMES, FEATURE_VERSION
from jvdata_bridge_mcp.ml.model import MODEL_FORMAT


def groups(frame: Any) -> np.ndarray:
    return frame.groupby("race_id", sort=False).size().to_numpy()


def calibrate_stages(probabilities: np.ndarray, frame: Any) -> list[float]:
    races = []
    offset = 0
    for _, race in frame.groupby("race_id", sort=False):
        order = np.argsort(race.finish.to_numpy())[:3]
        positions = race.finish.to_numpy()[order]
        p = probabilities[offset : offset + len(race)]
        offset += len(race)
        if list(positions) == [1, 2, 3]:
            races.append((np.log(np.maximum(p, 1e-15)), order))
    if not races:
        raise ValueError("no strict-order calibration races")
    result = []
    for stage in (1, 2):

        def loss(log_power: float) -> float:
            power = math.exp(log_power)
            return float(
                np.mean(
                    [
                        logsumexp(power * np.delete(log_p, order[:stage])) - power * log_p[order[stage]]
                        for log_p, order in races
                    ]
                )
            )

        fitted = minimize_scalar(loss, bounds=(-1.2, 1.2), method="bounded")
        result.append(float(math.exp(fitted.x)))
    return result


def model_payload(name: str, estimator: dict[str, Any], medians: np.ndarray, temperature: float = 1) -> dict[str, Any]:
    return {
        "format": MODEL_FORMAT,
        "model_version": f"market-free-{name}-{FEATURE_VERSION}",
        "feature_version": FEATURE_VERSION,
        "features": list(FEATURE_NAMES),
        "market_included": False,
        "impute": medians.tolist(),
        "temperature": temperature,
        "stage_powers": [1.0, 1.0],
        "estimator": estimator,
        "domain": {
            "surface": ["turf", "dirt"],
            "min_runners": 3,
            "max_runners": 18,
            "venue_codes": [f"{i:02d}" for i in range(1, 11)],
        },
        "calibration_status": "fitted_on_separate_period; assess_external_scores_and_curve",
        "training_policy": "completed_official_results_strictly_before_target_date; stored_card_version_unknown",
    }


def fold_ranges(year: int) -> dict[str, tuple[str, str]]:
    previous = year - 1
    return {
        "train": ("2018-01-01", f"{previous}-01-01"),
        "stopping": (f"{previous}-01-01", f"{previous}-04-01"),
        "provisional_calibration": (f"{previous}-04-01", f"{previous}-07-01"),
        "selection": (f"{previous}-07-01", f"{previous}-10-01"),
        "final_calibration": (f"{previous}-10-01", f"{year}-01-01"),
        "assessment": (f"{year}-01-01", f"{year + 1}-01-01"),
    }


def subset(frame: pd.DataFrame, interval: tuple[str, str]) -> pd.DataFrame:
    return frame[(frame.race_date >= interval[0]) & (frame.race_date < interval[1])].reset_index(drop=True)


def validate_partitions(parts: dict[str, pd.DataFrame]) -> None:
    seen: set[str] = set()
    previous_end = ""
    for name, frame in parts.items():
        if frame.empty:
            raise ValueError(f"empty temporal partition: {name}")
        races = set(frame.race_id)
        if races & seen or str(frame.race_date.min()) <= previous_end:
            raise ValueError("temporal partitions overlap or are out of order")
        seen.update(races)
        previous_end = str(frame.race_date.max())


def save_native(model: Any, payload: dict[str, Any], folder: Path) -> None:
    kind = payload["estimator"]["kind"]
    if kind == "lightgbm":
        model.booster_.save_model(str(folder / "native.txt"))
    elif kind == "catboost":
        model.save_model(str(folder / "native.cbm"))


def load_native(payload: dict[str, Any], folder: Path) -> Any:
    kind = payload["estimator"]["kind"]
    if kind == "lightgbm":
        import lightgbm as lgb

        return lgb.Booster(model_file=str(folder / "native.txt"))
    if kind == "catboost":
        from catboost import CatBoostRanker

        return CatBoostRanker().load_model(str(folder / "native.cbm"))
    return payload["estimator"]
