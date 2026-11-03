"""架空の確率だけで着順分布・券種確率の意味を検証する。"""

from __future__ import annotations

import math
from itertools import permutations

import pytest

from jvdata_bridge_mcp.ml.distribution import finish_distribution, ordered_top3, softmax, ticket_rows


@pytest.mark.parametrize("size", [1, 2, 3, 5, 18])
@pytest.mark.parametrize("powers", [(1, 1), (0.7, 1.3)])
def test_joint_marginals_and_all_ticket_normalizations(size: int, powers: tuple[float, float]) -> None:
    p = softmax([i * 0.3 for i in range(size)])
    dist = finish_distribution(list(range(1, size + 1)), p, stage_powers=powers)
    assert dist["joint_probability_sum"] == pytest.approx(1)
    marginal = list(dist["marginals"].values())
    for i, row in enumerate(marginal):
        assert row["win_probability"] == pytest.approx(p[i])
        assert 0 <= row["win_probability"] <= row["top2_probability"] + 1e-12
        assert row["top2_probability"] <= row["top3_probability"] + 1e-12 <= 1 + 1e-10
    assert sum(r["top2_probability"] for r in marginal) == pytest.approx(min(2, size))
    assert sum(r["top3_probability"] for r in marginal) == pytest.approx(min(3, size))
    sums = dist["ticket_probability_sums"]
    assert sums["quinella"] == pytest.approx(float(size >= 2))
    assert sums["wide"] == pytest.approx(math.comb(min(3, size), 2))
    assert sums["trio"] == sums["trifecta"] == pytest.approx(float(size >= 3))
    for group in ticket_rows(dist):
        assert len(group["items"]) <= 20
    assert all(len(set(order)) == len(order) for order in ordered_top3(p, powers))


def test_uniform_joint_probabilities_match_permutations_and_wide_event_is_not_unit_mass() -> None:
    dist = finish_distribution([1, 2, 3, 4, 5], [0.2] * 5)
    assert set(ordered_top3([0.2] * 5)) == set(permutations(range(5), 3))
    group = ticket_rows(dist, {"wide": ["1-2"]})[0]
    item = group["items"][0]
    assert item["probability"] == pytest.approx(0.3)
    assert item["normalized_selection_mass"] == pytest.approx(0.1)
    assert group["event_probability_sum"] == pytest.approx(3)


def test_invalid_and_inactive_ticket_requests_fail() -> None:
    dist = finish_distribution([1, 3, 5, 8], [0.1, 0.2, 0.3, 0.4])
    for requests in ({"trio": ["1-1-3"]}, {"wide": ["1-2"]}, {"unknown": ["1-3"]}, {}):
        with pytest.raises(ValueError):
            ticket_rows(dist, requests)


def test_softmax_remains_stable_for_large_utilities() -> None:
    p = softmax([10000, 10001, -10000], temperature=0.8)
    assert all(math.isfinite(x) and x > 0 for x in p)
    assert sum(p) == pytest.approx(1)
    assert p[1] > p[0] > p[2]
