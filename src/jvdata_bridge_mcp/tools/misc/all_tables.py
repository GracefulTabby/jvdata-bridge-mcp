"""許可schemaのrelation一覧を直接取得するMCPツール。"""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.sql_gateway import JvDataSqlGatewayProtocol


class RelationItem(BaseModel):
    """許可schemaに存在するrelationのメタデータ1件。"""

    model_config = ConfigDict(extra="forbid")

    table_schema: str = Field(description="schema名")
    relation_name: str = Field(description="relation名")
    relation_kind: str = Field(description="PostgreSQLのrelation種別")
    description: str = Field(description="relationの説明")


class SqlGetAllTablesOutput(BaseModel):
    """許可schemaに存在するrelationの一覧。"""

    tables: list[RelationItem] = Field(description="利用可能なrelation一覧")


def build_sql_get_all_tables_tool(
    database: JvDataDatabase,
    sql_gateway: JvDataSqlGatewayProtocol,
) -> Callable[..., object]:
    """SQLメタデータゲートウェイを注入したrelation一覧ツールを作る。"""

    del database

    async def sql_get_all_tables() -> SqlGetAllTablesOutput:
        """利用可能なread modelの一覧を取得する。

        許可されたschemaに存在するtable、view、materialized viewを返す。

        Returns:
            relationメタデータの生行一覧。
        """

        return SqlGetAllTablesOutput(tables=await sql_gateway.list_tables())

    return sql_get_all_tables
