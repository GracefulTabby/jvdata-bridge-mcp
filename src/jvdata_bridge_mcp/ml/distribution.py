"""重複しない着順の厳密な分布。市場オッズは参照しない。"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any


def softmax(values: Sequence[float], temperature: float = 1.0) -> list[float]:
    if not values or not math.isfinite(temperature) or temperature <= 0 or not all(math.isfinite(x) for x in values):
        raise ValueError("finite utilities and a positive temperature are required")
    maximum = max(values)
    weights = [math.exp(max(-700, (value - maximum) / temperature)) for value in values]
    total = math.fsum(weights)
    return [w / total for w in weights]


def ordered_top3(
    probabilities: Sequence[float],
    stage_powers: Sequence[float] = (1.0, 1.0),
) -> dict[tuple[int, ...], float]:
    """1着確率を保ち、2・3着の強弱だけを独立較正期間で補正できるPL拡張。"""
    p = list(probabilities)
    if not 1 <= len(p) <= 18 or any(not math.isfinite(x) or x <= 0 for x in p):
        raise ValueError("one to eighteen strictly positive runner probabilities required")
    if abs(math.fsum(p) - 1) > 1e-8:
        raise ValueError("winner probabilities must sum to one")
    if len(stage_powers) != 2 or any(not math.isfinite(x) or x <= 0 for x in stage_powers):
        raise ValueError("two positive stage powers required")
    weights = [[x**power for x in p] for power in stage_powers]
    result = {}
    for first, first_p in enumerate(p):
        if len(p) == 1:
            result[(first,)] = first_p
            continue
        denominator2 = math.fsum(w for i, w in enumerate(weights[0]) if i != first)
        for second in range(len(p)):
            if second == first:
                continue
            pair_p = first_p * weights[0][second] / denominator2
            if len(p) == 2:
                result[(first, second)] = pair_p
                continue
            denominator3 = math.fsum(w for i, w in enumerate(weights[1]) if i not in (first, second))
            for third in range(len(p)):
                if third not in (first, second):
                    result[(first, second, third)] = pair_p * weights[1][third] / denominator3
    return result


def finish_distribution(
    numbers: Sequence[int],
    probabilities: Sequence[float],
    *,
    stage_powers: Sequence[float] = (1.0, 1.0),
) -> dict[str, Any]:
    if len(numbers) != len(probabilities) or len(set(numbers)) != len(numbers):
        raise ValueError("runner numbers must be unique and match probabilities")
    joint = ordered_top3(probabilities, stage_powers)
    marginals = [[0.0, 0.0, 0.0] for _ in numbers]
    tickets: dict[str, dict[tuple[int, ...], float]] = {
        k: defaultdict(float) for k in ("quinella", "wide", "trio", "trifecta")
    }
    for order, probability in joint.items():
        horses = tuple(numbers[i] for i in order)
        for rank, index in enumerate(order):
            marginals[index][rank] += probability
        if len(order) >= 2:
            tickets["quinella"][tuple(sorted(horses[:2]))] += probability
        if len(order) == 3:
            tickets["trio"][tuple(sorted(horses))] += probability
            tickets["trifecta"][horses] += probability
        for pair in combinations(sorted(horses), 2):
            tickets["wide"][pair] += probability
    return {
        "marginals": {
            n: {"win_probability": p[0], "top2_probability": math.fsum(p[:2]), "top3_probability": math.fsum(p)}
            for n, p in zip(numbers, marginals, strict=True)
        },
        "tickets": {k: dict(v) for k, v in tickets.items()},
        "joint_probability_sum": math.fsum(joint.values()),
        "ticket_probability_sums": {k: math.fsum(v.values()) for k, v in tickets.items()},
        "stage_powers": list(stage_powers),
        "assumptions": [
            "strict finishing order; dead heats are not modelled",
            "conditional on supplied active runners",
            "nonfinishers have no separate event model; top-three outcomes require three classified finishers",
            "wide event probabilities sum to C(min(3,n),2); normalized selection mass is reported separately",
        ],
    }


def ticket_rows(
    distribution: Mapping[str, Any],
    requests: Mapping[str, Sequence[str]] | None = None,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """全組合せで正規化を確認し、応答は既定80件または指定200件以内に制限する。"""
    from jvdata_bridge_mcp.tools.odds.table import normalize_combinations

    if requests is not None and (not requests or sum(len(v) for v in requests.values()) > 200):
        raise ValueError("request one to 200 ticket combinations")
    result = []
    for bet_type, table in distribution["tickets"].items():
        total = math.fsum(table.values())
        if requests is not None:
            if bet_type not in requests:
                continue
            codes = normalize_combinations(bet_type, list(requests[bet_type]))
            keys = [tuple(int(code[i : i + 2]) for i in range(0, len(code), 2)) for code in codes]
            if any(key not in table for key in keys):
                raise ValueError("combination contains an inactive runner or is unavailable for field size")
        else:
            keys = sorted(table, key=lambda k: (-table[k], k))[:limit]
        result.append(
            {
                "bet_type": bet_type,
                "total_combinations": len(table),
                "event_probability_sum": total,
                "normalization": "multiple_winning_events" if bet_type == "wide" else "mutually_exclusive_outcomes",
                "returned_count": len(keys),
                "truncated": requests is None and len(keys) < len(table),
                "items": [
                    {
                        "combination": "".join(f"{n:02d}" for n in key),
                        "probability": table[key],
                        "normalized_selection_mass": table[key] / total if total else 0,
                    }
                    for key in keys
                ],
            }
        )
    if requests is not None and set(requests) - set(distribution["tickets"]):
        raise ValueError("supported ticket types: quinella, wide, trio, trifecta")
    return result
