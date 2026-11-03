"""馬別位置シナリオの結果遮断と確率分布の検証。"""

from __future__ import annotations

import pytest

from jvdata_bridge_mcp.tools.prediction._positions_coefficients import POSITION_HYBRID_MODELS
from jvdata_bridge_mcp.tools.prediction._positions_core import (
    EXPANDED_FEATURE_NAMES,
    POSITION_BAND_NAMES,
    simulate_hybrid_position_distributions,
    simulate_hybrid_position_ranks,
    simulate_position_ranks,
)
from jvdata_bridge_mcp.tools.prediction.positions import _JOCKEY_SQL, _SQL


def test_position_query_uses_only_previous_day_runs_and_no_target_market() -> None:
    """対象レースの結果や最終オッズを特徴量へ混ぜない。"""

    assert "h.race_date < c.race_date" in _SQL
    assert "h.is_final_result AND h.is_complete_result" in _SQL
    assert "h.horse_id = c.horse_id" in _SQL
    assert "final_win_odds" not in _SQL
    assert "final_win_popularity" not in _SQL
    assert "rr.race_id = h.race_id" in _SQL
    assert "rr.race_id = c.race_id" not in _SQL
    assert "e.race_date < t.race_date" in _JOCKEY_SQL
    assert "rr.race_id = e.race_id AND rr.horse_id = e.horse_id" in _JOCKEY_SQL
    assert "final_win_odds" not in _JOCKEY_SQL


def test_position_simulation_is_reproducible_and_allocates_one_leader_and_three_front_places() -> None:
    """確率はレース内で整合し、同じseedで再現できる。"""

    qualities = [0.8, 0.6, 0.5, 0.3, 0.2, 0.1, 0.45, 0.35]
    first = simulate_position_ranks(qualities, temperature=0.12, simulations=1000, seed=42)
    again = simulate_position_ranks(qualities, temperature=0.12, simulations=1000, seed=42)
    assert first == again
    assert abs(sum(row[0] for row in first) - 1) < 1e-12
    assert abs(sum(row[1] for row in first) - 3) < 1e-12
    assert first[0][0] > first[-1][0]
    assert all(0 <= row[0] <= row[1] <= 1 for row in first)


def test_hybrid_simulation_uses_lead_model_and_stays_coherent() -> None:
    """先頭専用モデルの重みを反映し、先頭・前3頭の枠数を守る。"""

    qualities = [0.1, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]
    lead_weights = [0.9, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01]
    rows = simulate_hybrid_position_ranks(qualities, lead_weights, temperature=0.16, simulations=2000, seed=123)
    assert rows == simulate_hybrid_position_ranks(
        qualities, lead_weights, temperature=0.16, simulations=2000, seed=123
    )
    assert rows[0][0] > 0.8
    assert abs(sum(row[0] for row in rows) - 1) < 1e-12
    assert abs(sum(row[1] for row in rows) - 3) < 1e-12
    assert all(0 <= row[0] <= row[1] <= 1 for row in rows)


def test_position_bands_and_alternative_leader_scenario_are_coherent() -> None:
    """位置帯は排他的で、先頭固定分岐でも全馬の順位が競合しない。"""

    qualities = [0.8, 0.6, 0.5, 0.3, 0.2, 0.1, 0.45, 0.35]
    lead_weights = [0.3, 0.2, 0.1, 0.1, 0.1, 0.1, 0.05, 0.05]
    rows = simulate_hybrid_position_distributions(qualities, lead_weights, temperature=0.16, simulations=1000, seed=42)
    assert all(tuple(row.band_probabilities) == POSITION_BAND_NAMES for row in rows)
    assert all(abs(sum(row.band_probabilities.values()) - 1) < 1e-12 for row in rows)
    assert all(row.lead_probability == row.band_probabilities["lead"] for row in rows)
    for row in rows:
        assert abs(sum(row.rank_probabilities) - 1) < 1e-12
        assert abs(sum((i + 1) * p for i, p in enumerate(row.rank_probabilities)) - row.expected_rank) < 1e-12
        low, high = row.rank_interval_80
        assert 1 <= low <= high <= len(qualities)
        assert sum(row.rank_probabilities[low - 1 : high]) >= 0.8
    assert abs(sum(row.band_probabilities["forward"] for row in rows) - 2) < 1e-12

    conditional = simulate_hybrid_position_distributions(
        qualities, lead_weights, temperature=0.16, simulations=1000, seed=43, forced_leader_index=1
    )
    assert conditional[1].lead_probability == 1
    assert conditional[1].expected_rank == 1
    assert conditional[1].rank_interval_80 == (1, 1)
    assert all(row.lead_probability == 0 for index, row in enumerate(conditional) if index != 1)
    assert abs(sum(row.top3_probability for row in conditional) - 3) < 1e-12

    paired = simulate_hybrid_position_distributions(
        qualities, lead_weights, temperature=0.16, simulations=1000, seed=44, forced_front_pair=(1, 2)
    )
    assert abs(paired[1].expected_rank + paired[2].expected_rank - 3) < 1e-12
    assert paired[1].top3_probability == paired[2].top3_probability == 1
    assert all(row.lead_probability == 0 for index, row in enumerate(paired) if index not in (1, 2))


def test_deployed_models_match_expanded_feature_schema() -> None:
    """係数の数と順序のずれで予測を暗黙に切り詰めない。"""

    assert len(EXPANDED_FEATURE_NAMES) == 38
    for stage in ("early", "fourth"):
        model = POSITION_HYBRID_MODELS[stage]
        assert len(model.rank_weights) == len(EXPANDED_FEATURE_NAMES)
        assert len(model.lead_weights) == len(EXPANDED_FEATURE_NAMES)


def test_optional_branches_skip_six_simulations_without_changing_primary_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from jvdata_bridge_mcp.tools.prediction import positions

    calls = []

    async def read(database: object, sql: str, *args: object, **kwargs: object) -> list[dict[str, object]]:
        if sql == positions._JOCKEY_SQL:
            return []
        return [
            dict(
                horse_number=n,
                horse_name=f"H{n}",
                horse_age=3,
                jockey_id=None,
                track_code="23",
                venue_code="06",
                distance_meters=1800,
                target_race_date="2026-09-22",
                history_field_size=None,
            )
            for n in range(1, 9)
        ]

    simulate = positions.simulate_hybrid_position_distributions

    def record(*args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return simulate(*args, **kwargs)

    monkeypatch.setattr(positions, "fetch_rows", read)
    monkeypatch.setattr(positions, "POSITION_SIMULATIONS", 100)
    monkeypatch.setattr(positions, "simulate_hybrid_position_distributions", record)
    short = asyncio.run(positions.forecast_position_scenarios(object(), "2026092206040709", include_branches=False))
    assert len(calls) == 2 and not short[2] and not short[3]
    calls.clear()
    full = asyncio.run(positions.forecast_position_scenarios(object(), "2026092206040709"))
    assert len(calls) == 8 and len(full[2]) == 3 and len(full[3]) == 3
    assert [h.model_dump() for h in short[0]] == [h.model_dump() for h in full[0]]
    assert short[1] == full[1] and short[4] == full[4]
