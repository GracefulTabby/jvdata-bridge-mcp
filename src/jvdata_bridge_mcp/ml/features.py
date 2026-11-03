"""学習と推論で共有する事前日特徴量。対象結果は入力カードに含めない。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

FEATURE_VERSION = "prior-date-market-free-v1"
COMPONENTS = (
    "aptitude",
    "recent_form",
    "distance_aptitude",
    "course_aptitude",
    "closing",
    "time_quality",
    "speed_quality",
    "jockey_form",
    "trainer_form",
    "horse_jockey_form",
)
EXTRA_FEATURES = (
    "history_count",
    "recent_count",
    "rest_days",
    "age",
    "female",
    "weight",
    "weight_change",
    "gate",
    "field_size",
    "distance",
    "distance_change",
    "dirt",
    "grade",
    "venue",
    "last_finish",
    "finish_trend",
    "last_margin",
    "mean_margin",
    "speed_coverage",
    "speed_missing",
    "early_frontness",
    "fourth_frontness",
    "early_lead_rate",
    "early_top3_rate",
    "rank_gain",
    "makuri_rate",
    "corner_count",
    "training_count",
    "training_best4f",
    "training_last1f",
    "training_gap",
    "training_change",
    "opponent_strength",
    "opponent_count",
    "course_pace_mean",
    "course_pace_std",
    "course_pace_count",
)
FEATURE_NAMES = (*[f"{n}_raw" for n in COMPONENTS], *[f"{n}_score" for n in COMPONENTS], *EXTRA_FEATURES)
CARD_FIELDS = {
    "race_id",
    "race_date",
    "horse_id",
    "horse_number",
    "horse_name",
    "horse_age",
    "sex_code",
    "jockey_id",
    "trainer_id",
    "assigned_weight_kg",
    "venue_code",
    "track_code",
    "distance_meters",
    "grade_code",
    "frame_number",
}
NAN = float("nan")


@dataclass(frozen=True)
class FeatureRecipe:
    recent_runs: int = 5
    decay: float = 0.72
    distance_bandwidth: float = 0
    condition_strength: float = 1
    training_days: int = 21


FEATURE_RECIPES = {
    "reference": FeatureRecipe(),
    "recent_fast_distance_kernel": FeatureRecipe(3, 0.5, 200, 0.6, 14),
    "recent_slow_distance_kernel": FeatureRecipe(8, 0.85, 400, 1.6, 35),
}


def distance_quality(runs: Sequence[Mapping[str, Any]], distance: float, bandwidth: float, strength: float) -> float:
    pairs = [
        (
            number(r.get("finish_quality")),
            math.exp(-0.5 * ((number(r.get("distance_meters")) - distance) / bandwidth) ** 2),
        )
        for r in runs
    ]
    pairs = [(quality, weight) for quality, weight in pairs if math.isfinite(quality + weight)]
    return (sum(quality * weight for quality, weight in pairs) + 0.5 * strength) / (
        sum(weight for _, weight in pairs) + strength
    )


def number(value: Any, default: float = NAN) -> float:
    if value is None:
        return default
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def nullable_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: nullable_values(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [nullable_values(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def day(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def valid_run(run: Mapping[str, Any]) -> bool:
    return all(run.get(k) is True for k in ("is_final_result", "is_complete_result", "is_starter", "is_finisher"))


def finish_quality(run: Mapping[str, Any]) -> float:
    n, rank = number(run.get("starters")), number(run.get("finish_position"))
    return (n - rank) / (n - 1) if n > 1 and 1 <= rank <= n else NAN


def corner(run: Mapping[str, Any], stage: str) -> float:
    """earlyは最初の記録コーナー1〜3。4しかなければ序盤は不明。"""
    keys = (
        ("corner_1_position", "corner_2_position", "corner_3_position") if stage == "early" else ("corner_4_position",)
    )
    for key in keys:
        value = number(run.get(key))
        if value >= 1:
            return value
    return NAN


def mean(values: Sequence[float], default: float = NAN) -> float:
    present = [v for v in values if math.isfinite(v)]
    return sum(present) / len(present) if present else default


def smoothed(
    runs: Sequence[Mapping[str, Any]],
    key: str,
    prior: float = 0.5,
    strength: float = 2,
    decay: float = 0.72,
) -> float:
    pairs = [(number(r.get(key)), decay**i) for i, r in enumerate(runs)]
    pairs = [(v, w) for v, w in pairs if math.isfinite(v)]
    return (sum(v * w for v, w in pairs) + prior * strength) / (sum(w for _, w in pairs) + strength)


def enrich_run(run: Mapping[str, Any]) -> dict[str, Any]:
    """完了した過去走だけに呼ぶ。対戦相手由来のclosing_qualityはデータ取得側で設定する。"""
    r = dict(run)
    r["finish_quality"] = finish_quality(r)
    margin = number(r.get("time_difference_seconds"))
    r["time_quality"] = math.exp(-max(0, margin)) if math.isfinite(margin) else NAN
    n = number(r.get("starters"))
    popularity = number(r.get("final_win_popularity"))
    r["market_quality"] = (n - popularity) / (n - 1) if n > 1 and popularity >= 1 else NAN
    early, fourth = corner(r, "early"), corner(r, "fourth")
    for stage, rank in (("early", early), ("fourth", fourth)):
        r[f"{stage}_frontness"] = (n - rank) / (n - 1) if n > 1 and 1 <= rank <= n else NAN
    r["early_lead"] = float(early == 1) if math.isfinite(early) else NAN
    r["early_top3"] = float(early <= 3) if math.isfinite(early) else NAN
    r["rank_gain"] = early - fourth
    r["makuri"] = float(early > 3 and fourth <= 3 and early - fourth >= 3) if math.isfinite(early + fourth) else NAN
    return r


def runner_features(
    card: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    jockey: Sequence[Mapping[str, Any]],
    trainer: Sequence[Mapping[str, Any]],
    speed: Mapping[str, Any] | None,
    training: Sequence[Mapping[str, Any]],
    *,
    field_size: int,
    pace_history: Sequence[float],
    feature_recipe: str = "reference",
) -> dict[str, float]:
    """履歴は新しい順。入力境界をここでも検査し、同日・未来を受け付けない。"""
    cutoff = day(card["race_date"])
    recipe = FEATURE_RECIPES[feature_recipe]
    if any(day(r["race_date"]) >= cutoff for rows in (history, jockey, trainer) for r in rows):
        raise ValueError("feature history must strictly precede target date")
    if any(day(t["training_date"]) >= cutoff for t in training):
        raise ValueError("training features must strictly precede target date")
    h = [r for r in history if valid_run(r)]
    recent = h[: recipe.recent_runs]
    surface = str(card.get("track_code") or "")[:1]
    distance = number(card.get("distance_meters"))
    same_distance = [
        r
        for r in h
        if str(r.get("track_code") or "")[:1] == surface
        and math.floor(number(r.get("distance_meters"), 0) / 200 + 0.5) == math.floor(distance / 200 + 0.5)
    ]
    same_course = [
        r for r in h if str(r.get("track_code") or "")[:1] == surface and r.get("venue_code") == card.get("venue_code")
    ]
    hj = [
        r
        for r in history
        if r.get("jockey_id") == card.get("jockey_id") and (cutoff - day(r["race_date"])).days <= 1095
    ][:20]
    j = [r for r in jockey if (cutoff - day(r["race_date"])).days <= 730][:30]
    t = [r for r in trainer if (cutoff - day(r["race_date"])).days <= 1095][:50]
    stored = number(speed.get("speed_quality_raw")) if speed else NAN
    count = number(speed.get("speed_observation_count"), 0) if speed else 0
    coverage = number(speed.get("speed_quality_coverage")) if speed else NAN
    speed_valid = math.isfinite(stored) and 0 <= stored <= 1 and count in range(6) and abs(coverage - count / 5) < 1e-8
    total = sum(0.72**i for i in range(int(count))) if speed_valid else 0
    quality = (stored * total + 1) / (total + 2) if speed_valid else NAN
    result = {
        "aptitude_raw": (sum(number(r.get("finish_position"), 99) <= 3 for r in h) + 1.25) / (len(h) + 5),
        "recent_form_raw": smoothed(recent, "finish_quality", strength=1.5, decay=recipe.decay),
        "distance_aptitude_raw": (
            distance_quality(
                [r for r in h if str(r.get("track_code") or "")[:1] == surface],
                distance,
                recipe.distance_bandwidth,
                5 * recipe.condition_strength,
            )
            if recipe.distance_bandwidth
            else smoothed(same_distance, "finish_quality", strength=5, decay=1)
        ),
        "course_aptitude_raw": (
            distance_quality(same_course, distance, recipe.distance_bandwidth, 6 * recipe.condition_strength)
            if recipe.distance_bandwidth
            else smoothed(same_course, "finish_quality", strength=6, decay=1)
        ),
        "closing_raw": smoothed(recent, "closing_quality", decay=recipe.decay),
        "time_quality_raw": smoothed(recent, "time_quality", decay=recipe.decay),
        "historical_market_raw": smoothed(recent, "market_quality"),
        "speed_quality_raw": quality,
        "jockey_form_raw": smoothed(j, "finish_quality", strength=8, decay=0.985),
        "trainer_form_raw": smoothed(t, "finish_quality", strength=12, decay=0.99),
        "horse_jockey_form_raw": smoothed(hj, "finish_quality", strength=4, decay=0.9),
        "history_count": float(len(h)),
        "recent_count": float(len(recent)),
        "rest_days": float((cutoff - day(h[0]["race_date"])).days) if h else NAN,
        "age": number(card.get("horse_age")),
        "female": float(str(card.get("sex_code")) == "2"),
        "weight": number(card.get("assigned_weight_kg")),
        "weight_change": number(card.get("assigned_weight_kg")) - number(h[0].get("assigned_weight_kg")) if h else NAN,
        "gate": number(card.get("horse_number")) / field_size,
        "field_size": float(field_size),
        "distance": distance,
        "distance_change": distance - number(h[0].get("distance_meters")) if h else NAN,
        "dirt": float(surface == "2"),
        "grade": float({"A": 1, "B": 2, "C": 3, "D": 4, "L": 4}.get(str(card.get("grade_code")), 0)),
        "venue": number(card.get("venue_code")),
        "last_finish": number(h[0].get("finish_quality")) if h else NAN,
        "finish_trend": mean([number(r.get("finish_quality")) for r in recent[:2]])
        - mean([number(r.get("finish_quality")) for r in recent[2:]]),
        "last_margin": number(h[0].get("time_difference_seconds")) if h else NAN,
        "mean_margin": mean([number(r.get("time_difference_seconds")) for r in recent]),
        "speed_coverage": coverage if speed_valid else NAN,
        "speed_missing": float(not speed_valid),
        "early_frontness": smoothed(recent, "early_frontness", decay=recipe.decay),
        "fourth_frontness": smoothed(recent, "fourth_frontness", decay=recipe.decay),
        "early_lead_rate": smoothed(recent, "early_lead", prior=0.08, decay=recipe.decay),
        "early_top3_rate": smoothed(recent, "early_top3", prior=0.25, decay=recipe.decay),
        "rank_gain": mean([number(r.get("rank_gain")) for r in recent]),
        "makuri_rate": mean([number(r.get("makuri")) for r in h[:20]]),
        "corner_count": float(sum(math.isfinite(number(r.get("early_frontness"))) for r in recent)),
        "opponent_strength": mean([number(r.get("opponent_strength")) for r in recent]),
        "opponent_count": float(sum(math.isfinite(number(r.get("opponent_strength"))) for r in recent)),
        "course_pace_mean": mean(list(pace_history)),
        "course_pace_std": NAN,
        "course_pace_count": float(len(pace_history)),
    }
    result.update(representative_pace_features(recent))
    if len(pace_history) > 1:
        result["course_pace_std"] = (
            sum((x - result["course_pace_mean"]) ** 2 for x in pace_history) / (len(pace_history) - 1)
        ) ** 0.5
    sessions = [s for s in training if (cutoff - day(s["training_date"])).days <= recipe.training_days]
    older = [s for s in training if recipe.training_days < (cutoff - day(s["training_date"])).days <= 180]
    clocks = [number(s.get("four_furlongs_seconds")) for s in sessions]
    clocks = [x for x in clocks if 35 <= x <= 80]
    result.update(
        {
            "training_count": float(len(sessions)),
            "training_best4f": min(clocks) if clocks else NAN,
            "training_last1f": mean([number(s.get("lap_200_0_seconds")) for s in sessions]),
            "training_gap": min((cutoff - day(s["training_date"])).days for s in sessions) if sessions else NAN,
            "training_change": mean(clocks) - mean([number(s.get("four_furlongs_seconds")) for s in older]),
        }
    )
    if feature_recipe != "reference":
        result.update(recipe_updates(card, h, training, feature_recipe))
    return result


def recipe_updates(
    card: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    training: Sequence[Mapping[str, Any]],
    feature_recipe: str,
) -> dict[str, float]:
    """変更する集計だけを生成する。再計算とライブ推論で同じ式を使う。"""
    recipe = FEATURE_RECIPES[feature_recipe]
    recent = history[: recipe.recent_runs]
    surface = str(card.get("track_code") or "")[:1]
    matching = [r for r in history if str(r.get("track_code") or "")[:1] == surface]
    same_course = [r for r in matching if r.get("venue_code") == card.get("venue_code")]
    result = {
        "recent_count": float(len(recent)),
        "recent_form_raw": smoothed(recent, "finish_quality", strength=1.5, decay=recipe.decay),
        "closing_raw": smoothed(recent, "closing_quality", decay=recipe.decay),
        "time_quality_raw": smoothed(recent, "time_quality", decay=recipe.decay),
        "finish_trend": mean([number(r.get("finish_quality")) for r in recent[:2]])
        - mean([number(r.get("finish_quality")) for r in recent[2:]]),
        "mean_margin": mean([number(r.get("time_difference_seconds")) for r in recent]),
        "early_frontness": smoothed(recent, "early_frontness", decay=recipe.decay),
        "fourth_frontness": smoothed(recent, "fourth_frontness", decay=recipe.decay),
        "early_lead_rate": smoothed(recent, "early_lead", prior=0.08, decay=recipe.decay),
        "early_top3_rate": smoothed(recent, "early_top3", prior=0.25, decay=recipe.decay),
        "rank_gain": mean([number(r.get("rank_gain")) for r in recent]),
        "corner_count": float(sum(math.isfinite(number(r.get("early_frontness"))) for r in recent)),
        "opponent_strength": mean([number(r.get("opponent_strength")) for r in recent]),
        "opponent_count": float(sum(math.isfinite(number(r.get("opponent_strength"))) for r in recent)),
    }
    if recipe.distance_bandwidth:
        distance = number(card.get("distance_meters"))
        result["distance_aptitude_raw"] = distance_quality(
            matching, distance, recipe.distance_bandwidth, 5 * recipe.condition_strength
        )
        result["course_aptitude_raw"] = distance_quality(
            same_course, distance, recipe.distance_bandwidth, 6 * recipe.condition_strength
        )
    cutoff = day(card["race_date"])
    sessions = [
        session for session in training if (cutoff - day(session["training_date"])).days <= recipe.training_days
    ]
    older = [
        session for session in training if recipe.training_days < (cutoff - day(session["training_date"])).days <= 180
    ]
    clocks = [number(session.get("four_furlongs_seconds")) for session in sessions]
    clocks = [value for value in clocks if 35 <= value <= 80]
    result.update(
        {
            "training_count": float(len(sessions)),
            "training_best4f": min(clocks) if clocks else NAN,
            "training_last1f": mean([number(session.get("lap_200_0_seconds")) for session in sessions]),
            "training_gap": min((cutoff - day(session["training_date"])).days for session in sessions)
            if sessions
            else NAN,
            "training_change": mean(clocks)
            - mean([number(session.get("four_furlongs_seconds")) for session in older]),
        }
    )
    return result


def representative_pace_features(recent: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    style_runs = []
    for run in recent[:5]:
        copy = dict(run)
        code = str(run.get("running_style_code") or "")
        copy["representative_front"] = float(code == "1") if code in {"1", "2", "3", "4"} else NAN
        copy["representative_forward"] = float(code in {"1", "2"}) if code in {"1", "2", "3", "4"} else NAN
        corner1, n = number(run.get("corner_1_position")), number(run.get("starters"))
        copy["representative_corner1"] = (n - corner1) / (n - 1) if n > 1 and corner1 >= 1 else NAN
        style_runs.append(copy)
    return {
        "representative_front": smoothed(style_runs, "representative_front", prior=0.1),
        "representative_forward": smoothed(style_runs, "representative_forward", prior=0.34),
        "representative_corner1": smoothed(style_runs, "representative_corner1"),
        "representative_style_known": float(
            any(str(r.get("running_style_code")) in {"1", "2", "3", "4"} for r in recent[:5])
        ),
    }


def add_field_ranks(rows: list[dict[str, float]]) -> None:
    """線形モデルSQLのPERCENT_RANK（同順位は最小順位）。欠測は欠測のまま。"""
    for name in (*COMPONENTS, "historical_market"):
        values = sorted(r[f"{name}_raw"] for r in rows if math.isfinite(r[f"{name}_raw"]))
        ranks = {value: values.index(value) / max(1, len(values) - 1) for value in set(values)}
        for row in rows:
            row[f"{name}_score"] = ranks.get(row[f"{name}_raw"], NAN)
