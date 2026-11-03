"""手書きSQLツールが共有する、直接実行用のランタイム基本処理。"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import TypeAlias

from sqlalchemy import text

from jvdata_bridge_mcp.database import JvDataDatabase

JsonRow: TypeAlias = dict[str, object]


@dataclass
class ForecastReadSession:
    cache: dict[str, asyncio.Task[list[JsonRow]]] = field(default_factory=dict)
    query_count: int = 0


_FORECAST_SESSION: ContextVar[ForecastReadSession | None] = ContextVar("jv_forecast_session", default=None)


@contextmanager
def forecast_read_session() -> Iterator[ForecastReadSession]:
    """一要求内だけで同じ読み取りを共有する。要求を跨ぐキャッシュは持たない。"""

    session = ForecastReadSession()
    token = _FORECAST_SESSION.set(session)
    try:
        yield session
    finally:
        _FORECAST_SESSION.reset(token)


def json_value(value: object) -> object:
    """PostgreSQLのスカラー値またはコンテナを、MCPで安全に扱える値へ変換する。"""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return str(value)


def json_row(row: Mapping[str, object]) -> JsonRow:
    """SQLAlchemyの1行分のマッピングを、MCPで扱える値へ変換する。"""

    return {str(key): json_value(value) for key, value in row.items()}


async def fetch_rows(
    database: JvDataDatabase,
    statement_sql: str,
    parameters: Mapping[str, object] | None = None,
    *,
    statement_timeout_seconds: int | None = None,
    disable_jit: bool = False,
) -> list[JsonRow]:
    """ツールが所有するSELECTを実行し、変換済みの行を返す。"""

    session = _FORECAST_SESSION.get()
    payload = dict(parameters or {})
    if session is not None:
        key = json.dumps([statement_sql, payload, statement_timeout_seconds], sort_keys=True, default=str)
        if key not in session.cache:
            session.query_count += 1
            session.cache[key] = asyncio.create_task(
                _fetch_db_rows(
                    database,
                    statement_sql,
                    payload,
                    statement_timeout_seconds=statement_timeout_seconds,
                    disable_jit=True,
                )
            )
        return copy.deepcopy(await session.cache[key])
    return await _fetch_db_rows(
        database, statement_sql, payload, statement_timeout_seconds=statement_timeout_seconds, disable_jit=disable_jit
    )


async def _fetch_db_rows(
    database: JvDataDatabase,
    statement_sql: str,
    payload: dict[str, object],
    *,
    statement_timeout_seconds: int | None,
    disable_jit: bool,
) -> list[JsonRow]:
    """実DBへの1回の読み取り。"""

    statement = text(statement_sql)
    if statement_timeout_seconds is None:
        async with database.read_connection() as connection:
            if disable_jit:
                await connection.execute(text("SET LOCAL jit = off"))
            result = await connection.execute(statement, payload)
    else:
        async with database.read_connection(statement_timeout_seconds=statement_timeout_seconds) as connection:
            if disable_jit:
                await connection.execute(text("SET LOCAL jit = off"))
            result = await connection.execute(statement, payload)
    return [json_row(row) for row in result.mappings().all()]
