"""馬別コーナー位置の特徴量と、検証済み線形モデルの軽量実行部。"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from jvdata_bridge_mcp.tools.prediction._positions_coefficients import (
    POSITION_HYBRID_MODEL_VERSION,
    POSITION_HYBRID_MODELS,
)

POSITION_MODEL_VERSION: Final[str] = POSITION_HYBRID_MODEL_VERSION
POSITION_SIMULATIONS: Final[int] = 5000
POSITION_BAND_NAMES: Final[tuple[str, ...]] = ("lead", "forward", "midfield", "rear")
FEATURE_NAMES: Final[tuple[str, ...]] = (
    "early_mean",
    "fourth_mean",
    "early_lead_rate",
    "early_top3_rate",
    "same_surface_early",
    "same_surface_fourth",
    "similar_distance_early",
    "position_change",
    "history_count",
    "gate",
    "field_size",
    "distance",
    "dirt",
)
EXPANDED_FEATURE_NAMES: Final[tuple[str, ...]] = (
    *FEATURE_NAMES,
    "horse_long_early",
    "horse_long_fourth",
    "horse_long_lead",
    "horse_long_top3",
    "horse_long_similar_early",
    "horse_long_count",
    "jockey_early",
    "jockey_lead",
    "jockey_top3",
    "jockey_count",
    "horse_rest_days",
    "horse_age",
    "other_front_pressure",
    "lead_pressure_interaction",
    "jockey_horse_interaction",
    *(f"venue_{venue:02d}" for venue in range(1, 11)),
)


def _smoothed(
    history: Sequence[Mapping[str, object]],
    key: str,
    *,
    surface: str | None = None,
    distance: int | None = None,
    prior: float = 0.5,
    strength: float = 2.0,
) -> float:
    total = 0.0
    weight_total = 0.0
    for index, run in enumerate(history):
        if surface is not None and run["surface"] != surface:
            continue
        if distance is not None and abs(int(run["distance"]) - distance) > 400:
            continue
        value = run.get(key)
        if value is None:
            continue
        weight = 0.72**index
        total += float(value) * weight
        weight_total += weight
    return (total + prior * strength) / (weight_total + strength)


def position_features(
    history: Sequence[Mapping[str, object]],
    *,
    field_size: int,
    horse_number: int,
    surface: str,
    distance: int,
) -> tuple[float, ...]:
    """新しい順の最大5走から、学習時と同じ13特徴量を計算する。"""

    early_mean = _smoothed(history, "early")
    fourth_mean = _smoothed(history, "fourth")
    return (
        early_mean,
        fourth_mean,
        _smoothed(history, "lead", prior=0.08, strength=2),
        _smoothed(history, "top3", prior=0.25, strength=2),
        _smoothed(history, "early", surface=surface),
        _smoothed(history, "fourth", surface=surface),
        _smoothed(history, "early", surface=surface, distance=distance),
        early_mean - fourth_mean,
        min(len(history), 5) / 5,
        (horse_number - 1) / max(field_size - 1, 1),
        field_size / 18,
        distance / 3000,
        float(surface == "dirt"),
    )


def _weighted_long(
    history: Sequence[Mapping[str, object]],
    key: str,
    *,
    prior: float,
    strength: float,
    decay: float,
    surface: str | None = None,
    distance: int | None = None,
) -> float:
    total = 0.0
    weight_total = 0.0
    for index, run in enumerate(history):
        if surface is not None and run["surface"] != surface:
            continue
        if distance is not None and abs(int(run["distance"]) - distance) > 400:
            continue
        weight = decay**index
        total += float(run[key]) * weight
        weight_total += weight
    return (total + prior * strength) / (weight_total + strength)


def horse_long_front_strength(history: Sequence[Mapping[str, object]]) -> float:
    """過去20走の先頭率を、履歴不足を補正して返す。"""

    return _weighted_long(history, "lead", prior=0.08, strength=5, decay=0.92)


def expanded_position_features(
    horse_history: Sequence[Mapping[str, object]],
    jockey_history: Sequence[Mapping[str, object]],
    *,
    race_date: date,
    field_size: int,
    horse_number: int,
    horse_age: int | None,
    surface: str,
    distance: int,
    venue_code: str,
    other_front_pressure: float,
) -> tuple[float, ...]:
    """学習時と同じ38特徴量を、馬20走・騎手50騎乗から作る。"""

    base = position_features(
        horse_history[:5], field_size=field_size, horse_number=horse_number, surface=surface, distance=distance
    )
    long_lead = horse_long_front_strength(horse_history)
    jockey_early = _weighted_long(jockey_history, "early", prior=0.5, strength=10, decay=0.98)
    previous_date = horse_history[0]["race_date"] if horse_history else None
    rest_days = (race_date - previous_date).days if isinstance(previous_date, date) else 90
    extra = (
        _weighted_long(horse_history, "early", prior=0.5, strength=5, decay=0.92),
        _weighted_long(horse_history, "fourth", prior=0.5, strength=5, decay=0.92),
        long_lead,
        _weighted_long(horse_history, "top3", prior=0.25, strength=5, decay=0.92),
        _weighted_long(horse_history, "early", prior=0.5, strength=4, decay=0.92, surface=surface, distance=distance),
        min(len(horse_history), 20) / 20,
        jockey_early,
        _weighted_long(jockey_history, "lead", prior=0.08, strength=10, decay=0.98),
        _weighted_long(jockey_history, "top3", prior=0.25, strength=10, decay=0.98),
        min(len(jockey_history), 50) / 50,
        min(rest_days, 365) / 365,
        (horse_age if horse_age is not None else 4) / 10,
        other_front_pressure,
        long_lead * other_front_pressure,
        long_lead * jockey_early,
        *(float(venue_code == f"{venue:02d}") for venue in range(1, 11)),
    )
    return (*base, *extra)


def predict_hybrid_position(feature_values: Sequence[float], stage: str) -> tuple[float, float]:
    """期待位置の品質と、先頭となる未正規化確率を返す。"""

    model = POSITION_HYBRID_MODELS[stage]
    quality = model.rank_intercept + sum(
        weight * value for weight, value in zip(model.rank_weights, feature_values, strict=True)
    )
    lead_logit = model.lead_intercept + sum(
        weight * value for weight, value in zip(model.lead_weights, feature_values, strict=True)
    )
    lead_logit = max(-40.0, min(40.0, lead_logit))
    return min(1.0, max(0.0, quality)), 1.0 / (1.0 + math.exp(-lead_logit))


def simulate_hybrid_position_ranks(
    qualities: Sequence[float],
    lead_weights: Sequence[float],
    *,
    temperature: float,
    simulations: int,
    seed: int,
) -> list[tuple[float, float, float]]:
    """先頭・前3頭の確率と期待順位を返す。"""

    distributions = simulate_hybrid_position_distributions(
        qualities,
        lead_weights,
        temperature=temperature,
        simulations=simulations,
        seed=seed,
    )
    return [(row.lead_probability, row.top3_probability, row.expected_rank) for row in distributions]


@dataclass(frozen=True, slots=True)
class SimulatedPositionDistribution:
    """仮想順位から集計した一頭分の周辺位置分布。"""

    lead_probability: float
    top3_probability: float
    expected_rank: float
    band_probabilities: dict[str, float]
    rank_probabilities: list[float]
    rank_interval_80: tuple[int, int]


def simulate_hybrid_position_distributions(
    qualities: Sequence[float],
    lead_weights: Sequence[float],
    *,
    temperature: float,
    simulations: int,
    seed: int,
    forced_leader_index: int | None = None,
    forced_front_pair: tuple[int, int] | None = None,
) -> list[SimulatedPositionDistribution]:
    """先頭候補を選び、または固定して、残る順位と位置帯を生成する。

    ``forced_leader_index`` は一頭を先頭に固定する。``forced_front_pair`` は
    二頭が最初の記録コーナーで前2頭にいる条件を固定する。出脚の競り合いが
    ペースへ与える影響までは推定しない。
    """

    count = len(qualities)
    if count < 2 or len(lead_weights) != count or simulations < 1 or temperature <= 0:
        raise ValueError("hybrid position simulation received invalid field or parameters")
    if forced_leader_index is not None and not 0 <= forced_leader_index < count:
        raise ValueError("forced_leader_index is outside the field")
    if forced_front_pair is not None:
        if (
            forced_leader_index is not None
            or len(set(forced_front_pair)) != 2
            or any(not 0 <= index < count for index in forced_front_pair)
        ):
            raise ValueError("forced_front_pair must contain two different runners")
    rng = random.Random(seed)
    lead_total = [0] * count
    top3_total = [0] * count
    rank_total = [0] * count
    band_totals = [[0] * len(POSITION_BAND_NAMES) for _ in range(count)]
    rank_totals = [[0] * count for _ in range(count)]
    forward_cutoff = math.ceil(count / 3)
    midfield_cutoff = math.ceil(2 * count / 3)
    for _ in range(simulations):
        if forced_front_pair is not None:
            leader = rng.choices(forced_front_pair, weights=[lead_weights[index] for index in forced_front_pair], k=1)[
                0
            ]
            second = next(index for index in forced_front_pair if index != leader)
            front = [leader, second]
        else:
            leader = (
                forced_leader_index
                if forced_leader_index is not None
                else rng.choices(range(count), weights=lead_weights, k=1)[0]
            )
            front = [leader]
        followers = sorted(
            (index for index in range(count) if index not in front),
            key=lambda index: (
                qualities[index] - temperature * math.log(-math.log(max(1e-12, min(1 - 1e-12, rng.random()))))
            ),
            reverse=True,
        )
        ranks = [*front, *followers]
        lead_total[leader] += 1
        for index in ranks[:3]:
            top3_total[index] += 1
        for rank, index in enumerate(ranks, start=1):
            rank_total[index] += rank
            rank_totals[index][rank - 1] += 1
            band_index = 0 if rank == 1 else 1 if rank <= forward_cutoff else 2 if rank <= midfield_cutoff else 3
            band_totals[index][band_index] += 1
    return [
        SimulatedPositionDistribution(
            lead_probability=lead_total[index] / simulations,
            top3_probability=top3_total[index] / simulations,
            expected_rank=rank_total[index] / simulations,
            band_probabilities={
                name: band_totals[index][band_index] / simulations
                for band_index, name in enumerate(POSITION_BAND_NAMES)
            },
            rank_probabilities=[value / simulations for value in rank_totals[index]],
            rank_interval_80=(_rank_quantile(rank_totals[index], 0.1), _rank_quantile(rank_totals[index], 0.9)),
        )
        for index in range(count)
    ]


def _rank_quantile(counts: Sequence[int], probability: float) -> int:
    """整数順位の経験分布から分位点を返す。"""

    target = sum(counts) * probability
    cumulative = 0
    for rank, value in enumerate(counts, start=1):
        cumulative += value
        if cumulative >= target:
            return rank
    return len(counts)


def simulate_position_ranks(
    qualities: Sequence[float],
    *,
    temperature: float,
    simulations: int,
    seed: int,
) -> list[tuple[float, float, float]]:
    """同じレース内で重複しない仮想順位を反復生成する。

    通過順原票には同順位があり得るため、これは位置帯の確率的近似である。
    """

    count = len(qualities)
    if count < 2 or simulations < 1 or temperature <= 0:
        raise ValueError("position simulation requires at least two runners, positive simulations and temperature")
    rng = random.Random(seed)
    lead = [0] * count
    top3 = [0] * count
    rank_total = [0] * count
    for _ in range(simulations):
        ranks = sorted(
            range(count),
            key=lambda index: (
                qualities[index] - temperature * math.log(-math.log(max(1e-12, min(1 - 1e-12, rng.random()))))
            ),
            reverse=True,
        )
        lead[ranks[0]] += 1
        for index in ranks[:3]:
            top3[index] += 1
        for rank, index in enumerate(ranks, start=1):
            rank_total[index] += rank
    return [
        (lead[index] / simulations, top3[index] / simulations, rank_total[index] / simulations)
        for index in range(count)
    ]
