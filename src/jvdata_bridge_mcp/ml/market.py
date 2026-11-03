"""能力モデルと独立した市場観測・払戻評価。期待値の保証はしない。"""

from __future__ import annotations

import math
from typing import Any


def expected_payout_multiple(probability: float, odds: float) -> float:
    if not math.isfinite(probability) or not 0 <= probability <= 1 or not math.isfinite(odds) or odds <= 0:
        raise ValueError("valid event probability and payout odds required")
    return probability * odds


def compare_observation(probability: float, observation: dict[str, Any], *, calibration_status: str) -> dict[str, Any]:
    """オッズは払戻込み倍率。控除率を重ねて差し引かない。最終変動は未知。"""
    odds = observation.get("odds")
    low, high = observation.get("odds_minimum"), observation.get("odds_maximum")
    multipliers = [
        expected_payout_multiple(probability, float(x)) for x in (odds, low, high) if x is not None and float(x) > 0
    ]
    return {
        "probability": probability,
        "expected_payout_multiple": multipliers[0] if odds and multipliers else None,
        "expected_payout_range": [min(multipliers), max(multipliers)] if multipliers and not odds else None,
        "status": "research_reference",
        "investment_value_confirmed": False,
        "calibration_status": calibration_status,
        "limitations": [
            "calibration error and distribution shift can reverse apparent advantage",
            "published odds may change before the final pool closes",
            "observed payout odds already include pool deductions",
            "an observed multiple above one is not evidence of profitable betting",
        ],
    }
