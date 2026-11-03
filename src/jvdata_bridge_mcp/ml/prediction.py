"""採用済み市場なしモデルを既存確率ツールへ接続する。"""

from __future__ import annotations

import math
from typing import Any

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.ml.distribution import finish_distribution, softmax, ticket_rows
from jvdata_bridge_mcp.ml.features import COMPONENTS, nullable_values
from jvdata_bridge_mcp.ml.inference import effective_temperature, predict_utilities
from jvdata_bridge_mcp.ml.live import live_features


def _nullable(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


async def ability_prediction(
    database: JvDataDatabase,
    race_code: str,
    model: dict[str, Any],
    combinations: dict[str, list[str]] | None,
) -> dict[str, Any]:
    cards, features, boundary, provenance = await live_features(
        database,
        race_code,
        feature_recipe=model.get("feature_recipe", "reference"),
        extended=model["feature_version"] == "prior-date-market-free-v2",
    )
    utilities = predict_utilities(model, features)
    temperature = effective_temperature(model, len(cards), features)
    p = softmax(utilities, temperature)
    numbers = [int(c["horse_number"]) for c in cards]
    dist = finish_distribution(numbers, p, stage_powers=model["stage_powers"])
    rows = []
    for i, (card, f) in enumerate(zip(cards, features, strict=True)):
        summary = provenance["summaries"][card["horse_id"]]
        raw = math.exp(max(-700, min(700, utilities[i] / temperature)))
        row = {
            "race_code": race_code,
            "history_before_date": str(card["race_date"]),
            "model_version": model["model_version"],
            "horse_number": card["horse_number"],
            "horse_id": card["horse_id"],
            "horse_name": card.get("horse_name"),
            "starts": summary["starts"],
            "average_finish_position": _nullable(summary["average_finish_position"]),
            "average_win_odds": None,
            "average_last_three_furlongs": _nullable(summary["average_last_three_furlongs"]),
            "history_reliability": 1 - math.exp(-summary["starts"] / 5),
            **{f"{name}_score": _nullable(f[f"{name}_score"]) for name in COMPONENTS},
            "historical_market_score": None,
            "speed_quality_coverage": _nullable(f["speed_coverage"]),
            "speed_baseline_level": None,
            "raw_score": raw,
            "win_probability": p[i],
            "prediction_source": "installed_empirically_approved_market_free_model",
            "top2_probability": dist["marginals"][numbers[i]]["top2_probability"],
            "top3_probability": dist["marginals"][numbers[i]]["top3_probability"],
        }
        rows.append(row)
    rows.sort(key=lambda r: (-r["win_probability"], r["horse_number"]))
    return {
        "probabilities": rows,
        "model_version": model["model_version"],
        "calibration_status": model["calibration_status"],
        "combination_probabilities": ticket_rows(dist, combinations),
        "distribution_checks": {
            "joint_probability_sum": dist["joint_probability_sum"],
            "ticket_probability_sums": dist["ticket_probability_sums"],
        },
        "provenance": nullable_values(
            {
                "boundary": boundary.model_dump(),
                "model": model.get("provenance"),
                "feature_recipe": model.get("feature_recipe", "reference"),
                "preprocessing": model.get("preprocessing"),
                **provenance,
            }
        ),
        "limitations": dist["assumptions"]
        + [
            "Training card publication times were unavailable; historical replay is not certified.",
            "Current withdrawal notices are applied; absence of a notice does not certify feed completeness.",
        ],
    }
