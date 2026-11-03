"""市場あり・市場なし派生値のSQL契約を検証する回帰試験。"""

from __future__ import annotations

from math import exp

import pytest

from jvdata_bridge_mcp.tools.misc.composite import _SQL as COMPOSITE_SQL
from jvdata_bridge_mcp.tools.misc.composite import CompositeAnalysisItem
from jvdata_bridge_mcp.tools.prediction._indicator_sql import (
    MARKET_FREE_MODEL_DESCRIPTION,
    MARKET_FREE_MODEL_VERSION,
)
from jvdata_bridge_mcp.tools.prediction.probabilities import _SQL as PROBABILITIES_SQL
from jvdata_bridge_mcp.tools.prediction.probabilities import HistoricalMarketDiagnostics, RaceProbabilityItem


def _softmax(values: list[float]) -> list[float]:
    """SQLのEXP(value)/SUM(EXP(value))と同じ計算を小さな入力で再現する。"""

    raw_scores = [exp(value) for value in values]
    total = sum(raw_scores)
    return [raw_score / total for raw_score in raw_scores]


def test_probability_contract_exposes_market_free_diagnostics_without_duplicate_aliases() -> None:
    """採用勝率と市場なし診断値を、それぞれの定義で返す。"""

    for field in (
        "model_version",
        "description",
        "raw_score",
        "utility",
        "support_utility",
        "probability",
        "support_delta",
    ):
        assert field in HistoricalMarketDiagnostics.model_fields

    assert "historical_market_diagnostics" in RaceProbabilityItem.model_fields
    assert "market_free_probability" not in RaceProbabilityItem.model_fields

    assert "raw_score / NULLIF(total_raw_score, 0) AS win_probability" in PROBABILITIES_SQL
    assert "market_inclusive_probability" not in RaceProbabilityItem.model_fields
    assert "market_inclusive_raw_score" not in RaceProbabilityItem.model_fields
    assert "market_free_raw_score / NULLIF(total_market_free_raw_score, 0) AS market_free_probability" in (
        PROBABILITIES_SQL
    )
    assert MARKET_FREE_MODEL_VERSION in PROBABILITIES_SQL
    assert MARKET_FREE_MODEL_DESCRIPTION in PROBABILITIES_SQL

    market_free_expression = PROBABILITIES_SQL.split("AS market_free_utility", maxsplit=1)[0].rsplit(",", 1)[-1]
    assert "historical_market_score" not in market_free_expression


def test_market_free_softmax_is_total_one_even_when_all_history_is_neutral() -> None:
    """履歴なしの中立効用でも市場あり・市場なし確率は欠損せず合計1になる。"""

    market_inclusive = _softmax([0.0, 0.0, 0.0])
    market_free = _softmax([0.0, 0.0, 0.0])

    assert all(value == pytest.approx(1 / 3) for value in market_inclusive)
    assert all(value == pytest.approx(1 / 3) for value in market_free)
    assert sum(market_inclusive) == pytest.approx(1.0)
    assert sum(market_free) == pytest.approx(1.0)
    assert all(
        inclusive - free == pytest.approx(0.0) for inclusive, free in zip(market_inclusive, market_free, strict=True)
    )


def test_composite_contract_exposes_market_free_score_and_diagnostic_provenance() -> None:
    """総合分析にも市場なし点・市場支持差分・診断版の由来を返す。"""

    for field in (
        "market_free_composite_score",
        "market_support_utility",
        "market_support_delta",
        "market_free_model_version",
        "market_free_model_description",
    ):
        assert field in CompositeAnalysisItem.model_fields

    assert "AS market_free_composite_score" in COMPOSITE_SQL
    assert "market_inclusive_composite_score" not in CompositeAnalysisItem.model_fields
    assert "s.historical_market_score * :historical_market_weight AS market_support_utility" in COMPOSITE_SQL
    assert "composite_score - market_free_composite_score AS market_support_delta" in COMPOSITE_SQL
    assert MARKET_FREE_MODEL_VERSION in COMPOSITE_SQL
    assert MARKET_FREE_MODEL_DESCRIPTION in COMPOSITE_SQL
