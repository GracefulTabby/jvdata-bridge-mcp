"""WH原票の有効範囲・特殊値を、本番の正規化SQLで検証する。"""

from __future__ import annotations

import re
import sqlite3

import pytest
import sqlglot

from jvdata_bridge_mcp.tools.race._weight_source import WEIGHT_CTES


def _regexp_like(value: str | None, pattern: str) -> bool | None:
    return None if value is None else re.search(pattern, value) is not None


@pytest.mark.parametrize(
    ("weight", "change", "sign", "expected_weight", "expected_change"),
    [
        ("002", "001", "+", 2, 1),
        ("998", "998", "-", 998, -998),
        ("500", "006", "+", 500, 6),
        ("500", "004", "-", 500, -4),
        ("500", "000", " ", 500, 0),
        ("500", "   ", " ", 500, None),
        ("500", "999", "+", 500, None),
        ("500", "999", "-", 500, None),
        ("500", "999", " ", 500, None),
        ("999", "999", " ", None, None),
        ("999", "000", "+", None, None),
        ("000", "006", "+", None, None),
        ("001", "000", " ", None, None),
        ("   ", "000", " ", None, None),
        ("50x", "006", "+", None, None),
        (None, "000", " ", None, None),
    ],
)
def test_wh_normalization_preserves_valid_values_and_nulls_special_values(
    weight: str | None,
    change: str,
    sign: str,
    expected_weight: int | None,
    expected_change: int | None,
) -> None:
    """WH仕様の999は計量不能、000の馬体重は取消。増減の空欄は欠損。"""

    # PostgreSQLの正規化CTEそのものをSQLiteへ変換し、DB接続なしで値を検証する。
    # 出力のモックやSQL文字列の一致では、特殊値をkgとして返す退行を検出できない。
    statement = sqlglot.parse_one("WITH " + WEIGHT_CTES + " SELECT * FROM wh_weights", read="postgres")
    weights = next(cte for cte in statement.find_all(sqlglot.exp.CTE) if cte.alias == "wh_weights")
    fixture = """
    WITH wh_fields(horse_number_text, body_weight_text, change_text, change_sign) AS (
        VALUES ('01', :weight, :change, :sign)
    )
    """
    with sqlite3.connect(":memory:") as connection:
        connection.create_function("REGEXP_LIKE", 2, _regexp_like)
        row = connection.execute(
            fixture + weights.this.sql(dialect="sqlite"),
            {"weight": weight, "change": change, "sign": sign},
        ).fetchone()

    assert row is not None
    assert row[1:3] == (expected_weight, expected_change)
