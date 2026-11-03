"""全頭の確率総和を保つ、履歴の浅さに対する正則化付き温度補正。"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.optimize import minimize

from jvdata_bridge_mcp.ml.research.calibration import calibrated_probabilities, fit_calibration, winner_scores
from jvdata_bridge_mcp.ml.research.common import groups

CALIBRATORS = ("identity", "temperature", "field_temperature", "reliability_temperature")


def apply(scores: np.ndarray, frame: Any, calibration: dict[str, Any]) -> np.ndarray:
    if not calibration.get("reliability_slope"):
        return calibrated_probabilities(scores, frame, calibration)
    sizes = groups(frame)
    starts = np.r_[0, np.cumsum(sizes)[:-1]]
    uncertainty = np.exp(-np.maximum(frame.history_count.to_numpy(dtype=float), 0) / 3)
    uncertainty = np.add.reduceat(uncertainty, starts) / sizes
    temperature = calibration["temperature"] * np.exp(
        calibration["slope"] * (sizes - 12) / 6 + calibration["reliability_slope"] * uncertainty
    )
    scaled = scores / np.repeat(temperature, sizes)
    maximum = np.maximum.reduceat(scaled, starts)
    exponentials = np.exp(scaled - np.repeat(maximum, sizes))
    return exponentials / np.repeat(np.add.reduceat(exponentials, starts), sizes)


def fit(scores: np.ndarray, frame: Any, kind: str) -> dict[str, Any]:
    if kind != "reliability_temperature":
        return {**fit_calibration(scores, frame, kind), "reliability_slope": 0.0}

    def objective(parameters: np.ndarray) -> float:
        calibration = {
            "temperature": float(np.exp(parameters[0])),
            "slope": float(parameters[1]),
            "reliability_slope": float(parameters[2]),
        }
        return winner_scores(frame, apply(scores, frame, calibration))["log_loss"] + 0.05 * float(
            parameters[1:] @ parameters[1:]
        )

    result = minimize(objective, np.zeros(3), method="L-BFGS-B", bounds=[(-1.5, 1.5), (-1, 1), (-1, 1)])
    if not result.success:
        raise ValueError(f"reliability calibration failed: {result.message}")
    return {
        "kind": kind,
        "temperature": float(np.exp(result.x[0])),
        "slope": float(result.x[1]),
        "reliability_slope": float(result.x[2]),
    }
