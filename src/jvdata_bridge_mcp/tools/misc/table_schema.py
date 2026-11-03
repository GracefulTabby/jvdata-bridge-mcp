"""read modelのスキーマを直接取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.sql_gateway import JvDataSqlGatewayProtocol

TableName = Annotated[str, Field(description="relation名。schema.relation形式を推奨")]


class TableSchemaColumn(BaseModel):
    """relationの列定義1件。"""

    model_config = ConfigDict(extra="forbid")

    column_name: str = Field(description="列名")
    data_type: str = Field(description="SQLのデータ型")
    udt_name: str = Field(description="PostgreSQLの内部型名")
    is_nullable: str = Field(description="NULL許容。YESまたはNO")
    column_default: str | None = Field(default=None, description="列のデフォルト式")
    ordinal_position: int = Field(description="列の順序")
    comment: str = Field(description="列コメント")
    table_description: str = Field(description="relationの説明")


class TableSchemaForeignKey(BaseModel):
    """relationの外部キー定義1件。"""

    model_config = ConfigDict(extra="forbid")

    constraint_name: str = Field(description="外部キー制約名")
    constrained_columns: list[str] = Field(description="制約対象列")
    referred_table: str = Field(description="参照先relation。schema.relation形式")
    referred_columns: list[str] = Field(description="参照先列")


class TableSchemaIndex(BaseModel):
    """relationの索引定義1件。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="索引名")
    is_unique: bool = Field(description="一意索引かどうか")
    indexdef: str = Field(description="PostgreSQLの索引定義")


class SqlGetTableSchemaOutput(BaseModel):
    """指定relationのスキーマ情報。"""

    table_schema: str = Field(description="schema名")
    relation_name: str = Field(description="relation名")
    description: str = Field(description="relationの説明")
    columns: list[TableSchemaColumn] = Field(description="列定義")
    primary_key: list[str] = Field(description="主キー列")
    foreign_keys: list[TableSchemaForeignKey] = Field(description="外部キー定義")
    indexes: list[TableSchemaIndex] = Field(description="索引定義")


def build_sql_get_table_schema_tool(
    database: JvDataDatabase,
    sql_gateway: JvDataSqlGatewayProtocol,
) -> Callable[..., object]:
    """SQLメタデータゲートウェイを注入したスキーマツールを作る。"""

    del database

    async def sql_get_table_schema(table_name: TableName) -> SqlGetTableSchemaOutput:
        """指定read modelの列定義を取得する。

        sql gatewayが許可schemaと識別子を検証したうえで、列・キー・索引を返す。

        Args:
            table_name: relation名。schema.relation形式を推奨。

        Returns:
            relationスキーマの生情報。
        """

        return SqlGetTableSchemaOutput.model_validate(await sql_gateway.table_schema(table_name))

    return sql_get_table_schema
