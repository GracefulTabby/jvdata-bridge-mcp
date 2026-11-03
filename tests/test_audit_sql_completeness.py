"""SQLの全件性と条件解決を検証する。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from jvdata_bridge_mcp.sql_gateway import JvDataSqlGateway, JvDataUnsafeQueryError, validate_custom_select
from jvdata_bridge_mcp.tools.misc.custom_sql import build_sql_execute_custom_query_tool


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM jv_mart.odds_history o WHERE o.race_date >= :a AND o.race_date < :b",
        "SELECT * FROM jv_mart.odds_history o WHERE :a <= o.race_date AND :b > o.race_date",
        "SELECT * FROM jv_mart.odds_history o WHERE o.race_id = :a OR o.race_id = :b",
        "WITH x AS (SELECT * FROM jv_mart.odds_history o WHERE o.race_id = :a) SELECT * FROM x",
    ],
)
def test_qualified_finite_odds_queries(sql: str) -> None:
    assert validate_custom_select(sql, allowed_schemas=frozenset({"jv_mart"}), pre_race_mode=False)


@pytest.mark.parametrize(
    "where",
    [
        "o.race_id = r.race_id",
        "r.race_date >= :a AND r.race_date < :b",
        "o.race_date >= :a AND r.race_date < :b",
        "o.race_id = :a OR o.odds > 10",
        "o.race_date > :a AND o.race_date > :b",
        "EXISTS (SELECT 1 FROM jv_mart.race_cards c WHERE c.race_id = :a)",
    ],
)
def test_unrelated_or_unbounded_conditions_do_not_guard_odds(where: str) -> None:
    with pytest.raises(JvDataUnsafeQueryError):
        validate_custom_select(
            f"SELECT o.* FROM jv_mart.odds_history o CROSS JOIN jv_data.races r WHERE {where}",
            allowed_schemas=frozenset({"jv_mart", "jv_data"}),
            pre_race_mode=False,
        )


class _Database:
    settings = SimpleNamespace(max_rows=200, command_timeout_seconds=1)

    @asynccontextmanager
    async def read_connection(self, **kwargs: Any) -> Any:
        yield self

    async def execute(self, statement: Any, payload: dict[str, Any] | None = None) -> Any:
        if payload is None:
            return None
        start = payload["jv_custom_offset"]
        rows = [{"n": n} for n in range(start, min(215, start + payload["jv_custom_limit"]))]
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))


def test_pagination_keeps_all_215_rows_and_reports_200_cap() -> None:
    async def run() -> None:
        db = _Database()
        tool = build_sql_execute_custom_query_tool(db, JvDataSqlGateway(db))
        sql = "SELECT race_id AS n FROM jv_data.races ORDER BY race_id"
        first = await tool(sql, max_rows=500)
        assert (first.requested_limit, first.effective_limit, first.returned_count) == (500, 200, 200)
        assert first.truncated and first.next_cursor
        last = await tool(sql, max_rows=500, cursor=first.next_cursor)
        assert not last.truncated and last.next_cursor is None
        assert len(first.rows + last.rows) == 215
        assert [r["n"] for r in first.rows + last.rows] == list(range(215))
        with pytest.raises(ValueError, match="cursor"):
            await tool(sql, max_rows=100, cursor=first.next_cursor)
        unordered = await tool("SELECT race_id FROM jv_data.races", max_rows=500)
        assert unordered.truncated and unordered.next_cursor is None

    asyncio.run(run())
