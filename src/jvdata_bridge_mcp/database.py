"""jvdata MCP専用の読み取り専用PostgreSQL接続。"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from threading import RLock
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from jvdata_bridge_mcp.config import JvDataSettings

_IDENTIFIER_PATTERN: re.Pattern[str] = re.compile(r"^[a-z_][a-z0-9_]*$")


class JvDataDatabaseError(RuntimeError):
    """jvdataデータベースアクセスの基底例外。"""


class JvDataSchemaError(JvDataDatabaseError):
    """想定したread modelや列が存在しない場合の例外。"""


def quote_identifier(identifier: str) -> str:
    """検証済みのPostgreSQL識別子を引用する。"""

    if not _IDENTIFIER_PATTERN.fullmatch(identifier):
        raise JvDataSchemaError(f"invalid database identifier: {identifier}")
    return f'"{identifier}"'


class JvDataDatabase:
    """MCP専用の有界接続プールとread-only transactionを管理する。"""

    def __init__(self, settings: JvDataSettings) -> None:
        self.settings: JvDataSettings = settings
        self._engine: AsyncEngine | None = None
        self._columns_cache: dict[tuple[str, str], tuple[str, ...]] = {}
        self._cache_lock: RLock = RLock()

    @property
    def engine(self) -> AsyncEngine:
        """遅延初期化された非同期SQLAlchemyエンジンを返す。"""

        if self._engine is None:
            connect_args: dict[str, object] = {
                "sslmode": self.settings.sslmode,
                "connect_timeout": self.settings.connect_timeout_seconds,
                "options": f"-c statement_timeout={self.settings.command_timeout_seconds * 1000}",
            }
            self._engine = create_async_engine(
                self.settings.sqlalchemy_url(),
                pool_size=self.settings.pool_size,
                max_overflow=self.settings.max_overflow,
                pool_timeout=self.settings.pool_timeout_seconds,
                pool_pre_ping=True,
                pool_recycle=300,
                connect_args=connect_args,
            )
        return self._engine

    @asynccontextmanager
    async def read_connection(
        self,
        *,
        statement_timeout_seconds: int | None = None,
    ) -> AsyncIterator[AsyncConnection]:
        """読み取り専用トランザクションの接続を非同期に1本取得する。"""

        async with self.engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            if statement_timeout_seconds is not None:
                timeout_milliseconds: int = max(1, statement_timeout_seconds) * 1000
                await connection.execute(
                    text("SELECT set_config('statement_timeout', :timeout, true)"),
                    {"timeout": str(timeout_milliseconds)},
                )
            yield connection

    async def relation_columns(
        self,
        schema: str,
        relation: str,
        connection: AsyncConnection | None = None,
    ) -> tuple[str, ...]:
        """relationの列名を取得し、プロセス内にキャッシュする。"""

        quote_identifier(schema)
        quote_identifier(relation)
        cache_key = (schema, relation)
        with self._cache_lock:
            cached = self._columns_cache.get(cache_key)
        if cached is not None:
            return cached

        statement = text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema
              AND table_name = :relation
            ORDER BY ordinal_position
            """
        )
        if connection is None:
            async with self.read_connection() as metadata_connection:
                result = await metadata_connection.execute(
                    statement,
                    {"schema": schema, "relation": relation},
                )
                rows = result.scalars().all()
        else:
            rows = (await connection.execute(statement, {"schema": schema, "relation": relation})).scalars().all()

        columns: tuple[str, ...] = tuple(str(value) for value in rows)
        if not columns:
            raise JvDataSchemaError(f"relation does not exist or has no columns: {schema}.{relation}")
        with self._cache_lock:
            self._columns_cache[cache_key] = columns
        return columns

    async def relation_exists(
        self,
        schema: str,
        relation: str,
        connection: AsyncConnection | None = None,
    ) -> bool:
        """relationが存在するかを確認する。"""

        quote_identifier(schema)
        quote_identifier(relation)
        statement = text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_class AS c
                JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema
                  AND c.relname = :relation
                  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
            )
            """
        )
        if connection is not None:
            result = await connection.execute(statement, {"schema": schema, "relation": relation})
            return bool(result.scalar())
        async with self.read_connection() as read_connection:
            result = await read_connection.execute(statement, {"schema": schema, "relation": relation})
            return bool(result.scalar())

    def clear_metadata_cache(self) -> None:
        """read modelの列キャッシュを破棄する。スキーマ更新後の再検査用。"""

        with self._cache_lock:
            self._columns_cache.clear()

    async def dispose(self) -> None:
        """接続プールを非同期に破棄する。"""

        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
