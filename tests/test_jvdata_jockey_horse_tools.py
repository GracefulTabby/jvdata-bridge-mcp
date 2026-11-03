"""騎手ランキングと競走馬比較のSQL契約を検証する。"""

from __future__ import annotations

import inspect

from jvdata_bridge_mcp.tools.horse.compare import _SQL as COMPARE_HORSES_SQL
from jvdata_bridge_mcp.tools.horse.compare import build_compare_horses_tool
from jvdata_bridge_mcp.tools.jockey.top import _SQL as TOP_JOCKEYS_SQL
from jvdata_bridge_mcp.tools.jockey.top import build_get_top_jockeys_tool


def test_top_jockeys_uses_overall_grain_and_a_minimum_sample() -> None:
    """当年・前年を累計へ混ぜず、少数出走の勝率をランキングから除外する。"""

    assert "s.period_index = 3" in TOP_JOCKEYS_SQL
    assert "s.condition_code = 'flat_total'" in TOP_JOCKEYS_SQL
    assert "WHERE starts >= :min_starts" in TOP_JOCKEYS_SQL
    assert "s.finish_index = 0" in TOP_JOCKEYS_SQL
    assert "s.finish_index <= 2" in TOP_JOCKEYS_SQL
    assert "MAX(s.flat_prize_money_yen)" in TOP_JOCKEYS_SQL
    assert "SUM(s.flat_prize_money_yen)" not in TOP_JOCKEYS_SQL


def test_top_jockeys_exposes_a_documented_minimum_sample_default() -> None:
    """公開関数の新しい標本数条件は既定30として明示される。"""

    tool = build_get_top_jockeys_tool(object())
    parameters = inspect.signature(tool).parameters

    assert parameters["min_starts"].default == 30
    assert "min_starts" in (tool.__doc__ or "")


def test_compare_horses_prefers_ids_and_falls_back_to_exact_names() -> None:
    """馬IDは同名馬を絞り、IDでないtokenだけが同名候補を全件返す。"""

    assert "WITH ORDINALITY" in COMPARE_HORSES_SQL
    assert "h.horse_id = q.token" in COMPARE_HORSES_SQL
    assert "h.horse_name = q.token" in COMPARE_HORSES_SQL
    assert "NOT EXISTS" in COMPARE_HORSES_SQL
    assert "id_match.horse_id = q.token" in COMPARE_HORSES_SQL
    assert "ORDER BY q.request_order, h.horse_id" in COMPARE_HORSES_SQL


def test_compare_horses_documents_same_name_disambiguation() -> None:
    """同名馬を区別するため馬IDを渡せることを公開説明へ含める。"""

    tool = build_compare_horses_tool(object())
    doc = tool.__doc__ or ""

    assert "馬ID" in doc
    assert "同名馬" in doc
