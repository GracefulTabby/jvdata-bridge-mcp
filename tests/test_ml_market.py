"""確率と市場を分離し、倍率1超を投資判断に変換しない。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from jvdata_bridge_mcp.ml.market import compare_observation, expected_payout_multiple
from jvdata_bridge_mcp.tools.odds import table


def test_expected_payout_uses_event_probability_without_double_deduction() -> None:
    assert expected_payout_multiple(0.2, 8) == pytest.approx(1.6)
    value = compare_observation(0.2, {"odds_minimum": 5, "odds_maximum": 8}, calibration_status="unverified")
    assert value["expected_payout_range"] == [1, 1.6]
    assert value["investment_value_confirmed"] is False
    assert value["status"] == "research_reference"


def test_odds_tool_compares_only_explicit_frozen_probability(monkeypatch: pytest.MonkeyPatch) -> None:
    from jvdata_bridge_mcp.tools import _observation_scope as temporal

    monkeypatch.setattr(temporal, "now_jst", lambda: datetime(2040, 6, 10, 12, tzinfo=temporal.JST))
    read = AsyncMock(
        return_value=[
            {
                "combination": "0103",
                "horse_number_1": 1,
                "horse_number_2": 3,
                "odds": 10.0,
                "odds_status": "available",
                "snapshot_at": "2040-06-10T11:59:00",
                "matching_row_count": 1,
            }
        ]
    )
    monkeypatch.setattr(table, "fetch_rows", read)
    result = asyncio.run(
        table.build_odds_table_tool(object())("2040061005010111", "wide", ["1-3"], model_probabilities={"1-3": 0.125})
    )
    comparison = result.items[0].probability_comparison
    assert comparison is not None
    assert comparison["expected_payout_multiple"] == 1.25
    assert comparison["investment_value_confirmed"] is False
    assert read.await_count == 1


def test_conflicting_aliases_of_a_combination_fail_before_reading() -> None:
    with pytest.raises(ValueError, match="conflicting"):
        asyncio.run(
            table.build_odds_table_tool(object())(
                "2040061005010111", "wide", model_probabilities={"1-3": 0.2, "0301": 0.3}
            )
        )
