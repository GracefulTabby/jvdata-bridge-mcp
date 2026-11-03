"""公式条件・過去時計・調教比較・出走構成を表す第2世代の共有特徴量。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from jvdata_bridge_mcp.ml.features import FEATURE_NAMES as BASE_FEATURE_NAMES
from jvdata_bridge_mcp.ml.features import day, mean, number, smoothed, valid_run
from jvdata_bridge_mcp.tools.race._class_facts import CLASS_RANKS

FEATURE_VERSION = "prior-date-market-free-v2"
EXTENSION_GROUPS = {
    "conditions": (
        "official_class_rank",
        "official_class_known",
        "class_change",
        "recent_class_mean",
        "recent_class_max",
        "same_class_quality",
        "track_detail",
        "surface_change",
    ),
    "ability": (
        "clock_residual_mean",
        "clock_residual_last",
        "clock_residual_best",
        "clock_reference_count",
        "closing_residual_mean",
        "clock_trend",
        "opponent_adjusted_quality",
        "high_pace_quality",
        "slow_pace_quality",
    ),
    "training": (
        "training_normalized_best4f",
        "training_normalized_mean4f",
        "training_normalized_last1f",
        "training_normalized_change",
        "training_acceleration",
        "training_reference_coverage",
        "training_context_count",
    ),
    "field": (
        "field_lead_pressure",
        "field_forward_pressure",
        "lead_competition",
        "front_competition",
        "pace_ability_interaction",
        "relative_clock_ability",
    ),
}
FEATURE_NAMES = (*BASE_FEATURE_NAMES, *(name for group in EXTENSION_GROUPS.values() for name in group))
NAN = float("nan")


def official_class(codes: Mapping[str, Any] | None, age: Any) -> float:
    """複数版で不一致の値は取得側でnullにし、年齢に該当する公式コードだけを使う。"""
    if not codes:
        return NAN
    value = number(age)
    if not math.isfinite(value):
        return NAN
    key = "5+" if value >= 5 else str(int(value))
    return float(CLASS_RANKS.get(str(codes.get(key)), NAN))


def moments(values: Sequence[float], floor: float) -> tuple[float, float, int]:
    finite = [float(x) for x in values if math.isfinite(x)]
    if len(finite) < 20:
        return NAN, NAN, len(finite)
    center = math.fsum(finite) / len(finite)
    variance = math.fsum((x - center) ** 2 for x in finite) / (len(finite) - 1)
    return center, max(floor, math.sqrt(variance)), len(finite)


def clock_context(rows: Sequence[Mapping[str, Any]], race_date: Any, ground: Any) -> dict[str, float]:
    """同場・同走路・同距離の先行400レース。馬場一致20件以上ならその部分を使う。"""
    cutoff = day(race_date)
    prior = [r for r in rows if day(r["race_date"]) < cutoff][-400:]
    matching = [r for r in prior if r.get("ground") == ground and ground is not None]
    selected = matching if sum(math.isfinite(number(r.get("winner_seconds"))) for r in matching) >= 20 else prior
    result = {}
    for name, field, floor in (
        ("clock", "winner_seconds", 0.5),
        ("closing", "winner_closing", 0.2),
        ("pace", "pace_seconds", 0.3),
    ):
        center, scale, count = moments([number(r.get(field)) for r in selected], floor)
        result.update({f"{name}_mean": center, f"{name}_std": scale, f"{name}_count": float(count)})
    return result


def clock_values(run: Mapping[str, Any], context: Mapping[str, float]) -> dict[str, float]:
    result = {}
    for output, input_name, prefix in (
        ("clock_residual", "elapsed_seconds", "clock"),
        ("closing_residual", "last_three_furlongs_seconds", "closing"),
        ("historical_pace_z", "first_three_furlongs_seconds", "pace"),
    ):
        value, scale = number(run.get(input_name)), context[f"{prefix}_std"]
        raw = (context[f"{prefix}_mean"] - value) / scale if math.isfinite(value + scale) and scale > 0 else NAN
        result[output] = max(-10.0, min(10.0, raw)) if math.isfinite(raw) else NAN
    result["clock_reference_count"] = context["clock_count"]
    return result


def training_z(value: Any, count: Any, total: Any, square: Any) -> float:
    """種別・場所・コース別の先行365日の母集団で標準化する。"""
    value, count, total, square = (number(x) for x in (value, count, total, square))
    if not math.isfinite(value + count + total + square) or count < 30:
        return NAN
    center = total / count
    deviation = math.sqrt(max(0, (square - total * center) / max(1, count - 1)))
    return max(-10.0, min(10.0, (center - value) / max(0.2, deviation)))


def runner_extensions(
    card: Mapping[str, Any], history: Sequence[Mapping[str, Any]], training: Sequence[Mapping[str, Any]]
) -> dict[str, float]:
    cutoff = day(card["race_date"])
    if any(day(r["race_date"]) >= cutoff for r in history) or any(day(r["training_date"]) >= cutoff for r in training):
        raise ValueError("extended evidence must strictly precede target date")
    h = [r for r in history if valid_run(r)]
    recent = h[:5]
    rank = number(card.get("official_class_rank"))
    classes = [number(r.get("official_class_rank")) for r in recent]
    clocks = [number(r.get("clock_residual")) for r in recent]
    same_class = [r for r in h if math.isfinite(rank) and number(r.get("official_class_rank")) == rank]
    current = [r for r in training if (cutoff - day(r["training_date"])).days <= 21]
    previous = [r for r in training if 21 < (cutoff - day(r["training_date"])).days <= 180]
    z4 = [number(r.get("normalized_4f")) for r in current]
    finite_clocks = [x for x in clocks if math.isfinite(x)]
    finite_classes = [x for x in classes if math.isfinite(x)]
    finite_z4 = [x for x in z4 if math.isfinite(x)]
    adjusted = [number(r.get("finish_quality")) + 0.5 * (number(r.get("opponent_strength")) - 0.5) for r in recent]
    result = {
        "official_class_rank": rank,
        "official_class_known": float(math.isfinite(rank)),
        "class_change": rank - number(h[0].get("official_class_rank")) if h else NAN,
        "recent_class_mean": mean(classes),
        "recent_class_max": max(finite_classes) if finite_classes else NAN,
        "same_class_quality": smoothed(same_class, "finish_quality", strength=5),
        "track_detail": number(card.get("track_code")),
        "surface_change": float(str(card.get("track_code"))[:1] != str(h[0].get("track_code"))[:1]) if h else NAN,
        "clock_residual_mean": mean(clocks),
        "clock_residual_last": clocks[0] if clocks else NAN,
        "clock_residual_best": max(finite_clocks) if finite_clocks else NAN,
        "clock_reference_count": mean([number(r.get("clock_reference_count")) for r in recent]),
        "closing_residual_mean": mean([number(r.get("closing_residual")) for r in recent]),
        "clock_trend": mean(clocks[:2]) - mean(clocks[2:]),
        "opponent_adjusted_quality": mean(adjusted),
        "high_pace_quality": smoothed(
            [r for r in h[:20] if number(r.get("historical_pace_z")) >= 0.5], "finish_quality", strength=5
        ),
        "slow_pace_quality": smoothed(
            [r for r in h[:20] if number(r.get("historical_pace_z")) <= -0.5], "finish_quality", strength=5
        ),
        "training_normalized_best4f": max(finite_z4) if finite_z4 else NAN,
        "training_normalized_mean4f": mean(z4),
        "training_normalized_last1f": mean([number(r.get("normalized_last1f")) for r in current]),
        "training_normalized_change": mean(z4) - mean([number(r.get("normalized_4f")) for r in previous]),
        "training_acceleration": mean(
            [number(r.get("lap_400_200_seconds")) - number(r.get("lap_200_0_seconds")) for r in current]
        ),
        "training_reference_coverage": len(finite_z4) / len(current) if current else NAN,
        "training_context_count": float(
            len({(r.get("training_type"), r.get("training_center_code"), r.get("course_code")) for r in current})
        ),
    }
    result.update({name: NAN for name in EXTENSION_GROUPS["field"]})
    return result


def add_field_extensions(rows: list[dict[str, float]]) -> None:
    """現在の全出走馬を使う。対象結果・市場は使わない。"""
    for index, row in enumerate(rows):
        others = [r for i, r in enumerate(rows) if i != index]
        lead = math.fsum(r["early_lead_rate"] for r in others)
        forward = math.fsum(r["early_top3_rate"] for r in others)
        row.update(
            {
                "field_lead_pressure": lead,
                "field_forward_pressure": forward,
                "lead_competition": row["early_lead_rate"] * lead,
                "front_competition": row["early_frontness"] * forward,
                "pace_ability_interaction": (row["high_pace_quality"] - row["slow_pace_quality"]) * lead,
                "relative_clock_ability": row["clock_residual_mean"]
                - mean([r["clock_residual_mean"] for r in others]),
            }
        )
