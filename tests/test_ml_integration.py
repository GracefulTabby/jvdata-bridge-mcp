"""市場なし導入モデルの既存MCP出力を、架空の3頭で検証する。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvdata_bridge_mcp.ml.features import FEATURE_NAMES, FEATURE_VERSION, add_field_ranks, runner_features
from jvdata_bridge_mcp.ml.model import MODEL_FORMAT
from jvdata_bridge_mcp.tools._race_boundary import RaceBoundary
from jvdata_bridge_mcp.tools.prediction import probabilities


def test_installed_market_free_model_handles_missing_speed_and_serializes_existing_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jvdata_bridge_mcp.ml import prediction

    n = len(FEATURE_NAMES)
    model = {
        "format": MODEL_FORMAT,
        "feature_version": FEATURE_VERSION,
        "features": list(FEATURE_NAMES),
        "market_included": False,
        "model_version": "synthetic",
        "impute": [0.0] * n,
        "temperature": 1,
        "stage_powers": [1, 1],
        "calibration_status": "synthetic_only",
        "estimator": {"kind": "linear", "weights": [0.0] * n, "intercept": 0, "center": [0.0] * n, "scale": [1.0] * n},
    }
    cards = [
        {
            "race_date": "2040-06-10",
            "horse_number": i,
            "horse_id": f"synthetic{i}",
            "horse_name": f"S{i}",
            "horse_age": 3,
            "track_code": "11",
            "distance_meters": 1600,
            "venue_code": "05",
        }
        for i in range(1, 4)
    ]
    values = [runner_features(c, [], [], [], None, [], field_size=3, pace_history=[]) for c in cards]
    add_field_ranks(values)
    boundary = RaceBoundary(
        race_code="2040061005010111",
        race_date="2040-06-10",
        analysis_at="2040-06-10T10:00:00+09:00",
        history_before_at="2040-06-10T10:00:00+09:00",
    )

    async def features(*args: object, **kwargs: object) -> tuple:
        return (
            cards,
            values,
            boundary,
            {
                "summaries": {
                    c["horse_id"]: {
                        "starts": 0,
                        "average_finish_position": float("nan"),
                        "average_last_three_furlongs": float("nan"),
                    }
                    for c in cards
                }
            },
        )

    monkeypatch.setattr(prediction, "live_features", features)
    monkeypatch.setattr(probabilities, "load_active_model", lambda: model)
    result = asyncio.run(probabilities.build_predict_race_probabilities_tool(object())("2040061005010111"))
    assert len(result.probabilities) == 3
    assert sum(p.win_probability for p in result.probabilities) == pytest.approx(1)
    assert all(p.top3_probability == pytest.approx(1) for p in result.probabilities)
    assert all(p.speed_quality_score is None for p in result.probabilities)
    assert "market_inclusive_probability" not in result.probabilities[0].model_dump()
    assert result.model_version == "synthetic"
    assert result.probabilities[0].historical_market_diagnostics is None
    assert "market_free_probability" not in result.probabilities[0].model_dump()
    encoded = result.model_dump_json()
    assert "NaN" not in encoded
    assert json.loads(encoded)["provenance"]["summaries"]["synthetic1"]["average_finish_position"] is None


def test_linear_diagnostics_and_ticket_probabilities_use_the_active_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jvdata_bridge_mcp.ml import live
    from jvdata_bridge_mcp.ml.features import COMPONENTS

    rows = []
    for number, (win, diagnostic) in enumerate(zip((0.4, 0.3, 0.2, 0.1), (0.35, 0.25, 0.25, 0.15), strict=True), 1):
        rows.append(
            {
                "race_code": "2040061005010111",
                "horse_number": number,
                "horse_id": f"synthetic{number}",
                "starts": 1,
                "history_reliability": 0.2,
                "speed_quality_coverage": 1.0,
                **{f"{name}_score": 0.5 for name in COMPONENTS},
                "historical_market_score": 0.5,
                "raw_score": 1.0,
                "win_probability": win,
                "market_free_model_version": "synthetic-diagnostic",
                "market_free_model_description": "synthetic",
                "market_free_raw_score": 1.0,
                "market_free_utility": 0.0,
                "market_free_probability": diagnostic,
                "market_support_utility": 0.0,
            }
        )
    provenance = SimpleNamespace(model_dump=lambda: {})
    monkeypatch.setattr(probabilities, "load_active_model", lambda: None)
    monkeypatch.setattr(
        live,
        "current_roster",
        AsyncMock(
            return_value=(
                [{"horse_number": number} for number in (1, 2, 3)],
                provenance,
                provenance,
            )
        ),
    )
    monkeypatch.setattr(probabilities, "ensure_speed_quality_available", AsyncMock())
    monkeypatch.setattr(probabilities, "fetch_rows", AsyncMock(return_value=rows))
    result = asyncio.run(probabilities.build_predict_race_probabilities_tool(object())("2040061005010111"))
    assert [row.horse_number for row in result.probabilities] == [1, 2, 3]
    assert sum(row.win_probability for row in result.probabilities) == pytest.approx(1)
    diagnostics = [row.historical_market_diagnostics for row in result.probabilities]
    assert sum(row.probability for row in diagnostics) == pytest.approx(1)
    assert diagnostics[0].support_delta == pytest.approx(0.4 / 0.9 - 0.35 / 0.85)
    assert all(row.top3_probability == pytest.approx(1) for row in result.probabilities)
    assert result.distribution_checks["joint_probability_sum"] == pytest.approx(1)
    assert all(
        "04" not in item["combination"] for group in result.combination_probabilities for item in group["items"]
    )
