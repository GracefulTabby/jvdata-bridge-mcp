"""レース内の正規化を保つ較正。候補選択用と最終較正用の期間を分ける。"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.optimize import minimize

from jvdata_bridge_mcp.ml.research.common import groups

CALIBRATORS = ("identity", "temperature", "field_temperature")


def calibrated_probabilities(scores: np.ndarray, frame: Any, calibration: dict[str, Any]) -> np.ndarray:
    sizes = groups(frame)
    starts = np.r_[0, np.cumsum(sizes)[:-1]]
    temperature = calibration["temperature"] * np.exp(calibration["slope"] * (sizes - 12) / 6)
    scaled = scores / np.repeat(temperature, sizes)
    maximum = np.maximum.reduceat(scaled, starts)
    exponentials = np.exp(scaled - np.repeat(maximum, sizes))
    return exponentials / np.repeat(np.add.reduceat(exponentials, starts), sizes)


def winner_scores(frame: Any, probabilities: np.ndarray) -> dict[str, float]:
    sizes = groups(frame)
    starts = np.r_[0, np.cumsum(sizes)[:-1]]
    labels = frame.win.to_numpy(dtype=float).copy()
    labels /= np.repeat(np.add.reduceat(labels, starts), sizes)
    return {
        "log_loss": float(np.mean(-np.add.reduceat(labels * np.log(np.maximum(probabilities, 1e-15)), starts))),
        "brier": float(np.mean(np.add.reduceat((probabilities - labels) ** 2, starts) / sizes)),
    }


def fit_calibration(scores: np.ndarray, frame: Any, kind: str) -> dict[str, Any]:
    if kind not in CALIBRATORS:
        raise ValueError("unknown calibration method")
    if kind == "identity":
        return {"kind": kind, "temperature": 1.0, "slope": 0.0}

    def objective(parameters: np.ndarray) -> float:
        slope = float(parameters[1]) if len(parameters) == 2 else 0.0
        calibration = {"temperature": float(np.exp(parameters[0])), "slope": slope}
        loss = winner_scores(frame, calibrated_probabilities(scores, frame, calibration))["log_loss"]
        return loss + 0.05 * slope**2

    bounds = [(-1.5, 1.5)] + ([(-1, 1)] if kind == "field_temperature" else [])
    fit = minimize(objective, np.zeros(len(bounds)), bounds=bounds, method="L-BFGS-B")
    if not fit.success:
        raise ValueError(f"calibration failed: {fit.message}")
    return {"kind": kind, "temperature": float(np.exp(fit.x[0])), "slope": float(fit.x[1]) if len(fit.x) == 2 else 0.0}
