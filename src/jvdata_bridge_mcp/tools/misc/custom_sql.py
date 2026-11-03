"""sqlglot検証済みのカスタムSELECTを直接実行するMCPツール。"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Callable, Final

from pydantic import BaseModel, Field
from sqlglot import parse_one

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.sql_gateway import JvDataSqlGateway

SqlText = Annotated[str, Field(description="許可されたread modelを参照する単一のSELECTまたはWITH SQL")]
MaxRows = Annotated[int, Field(description="返却する最大行数")]
ParametersJson = Annotated[str, Field(description="SQLバインドパラメータのJSONオブジェクト")]
PreRaceMode = Annotated[bool, Field(description="発走前に利用可能なread modelだけへ制限するか")]
SqlContext = Annotated[
    str | None,
    Field(description="SQL実行コンテキスト。development、pre_race_prediction、post_race_reviewのいずれか"),
]

_SUPPORTED_CONTEXTS: Final[frozenset[str]] = frozenset({"development", "pre_race_prediction", "post_race_review"})


def _effective_pre_race_mode(context: str | None, pre_race_mode: bool) -> bool:
    """実行コンテキストを検証し、発走前の参照制約を決める。"""

    if context is not None and (not isinstance(context, str) or context not in _SUPPORTED_CONTEXTS):
        supported = ", ".join(sorted(_SUPPORTED_CONTEXTS))
        raise ValueError(f"unsupported context: {context!r}; use one of: {supported}")
    return pre_race_mode or context == "pre_race_prediction"


class SqlExecuteCustomQueryOutput(BaseModel):
    """検証済みSELECTの動的な行一覧。"""

    rows: list[dict[str, object]] = Field(description="検証済みSELECTが返した列名付きの行")
    requested_limit: int
    effective_limit: int
    returned_count: int
    truncated: bool
    next_cursor: str | None = None
    pagination_note: str = "一意なORDER BYが必要。ページ間のDB更新による重複/欠落は保証しない。"


def build_sql_execute_custom_query_tool(
    database: JvDataDatabase,
    sql_gateway: JvDataSqlGateway,
) -> Callable[..., object]:
    """共有DBとsqlglotゲートウェイを注入したSQLツールを作る。"""

    del database

    async def sql_execute_custom_query(
        sql: SqlText,
        max_rows: MaxRows = 100,
        parameters_json: ParametersJson = "",
        pre_race_mode: PreRaceMode = False,
        context: SqlContext = None,
        cursor: Annotated[str | None, Field(description="同じSQL・パラメータ・上限で継続するカーソル")] = None,
    ) -> SqlExecuteCustomQueryOutput:
        """許可されたread modelだけを対象にSQLを実行する。

        SQLはsqlglotで単一のSELECT/CTEとして解析され、許可schema、read-only、
        行数上限、expensive relationの条件をゲートウェイ側で検証する。

        Args:
            sql: 実行する単一SELECTまたはWITH SQL。
            max_rows: 返却する最大行数。
            parameters_json: 名前付きバインド値のJSONオブジェクト。
            pre_race_mode: 発走前に利用可能なread modelだけに制限するか。
            context: SQL実行コンテキスト。未指定時はpre_race_modeの値を使う。

        Returns:
            検証済みSELECTが返した列名付きの行一覧。
        """

        effective_pre_race_mode = _effective_pre_race_mode(context, pre_race_mode)
        parameters: Mapping[str, object] = {}
        if parameters_json:
            decoded = json.loads(parameters_json)
            if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
                raise ValueError("parameters_json must be a JSON object")
            parameters = decoded
        identity = hashlib.sha256(
            json.dumps(
                [sql, parameters, max_rows, effective_pre_race_mode], sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
        ordered = parse_one(sql, read="postgres").args.get("order") is not None
        offset = 0
        if cursor:
            try:
                page_state = json.loads(base64.urlsafe_b64decode(cursor))
                offset = page_state["offset"]
                if page_state["query"] != identity or type(offset) is not int or offset < 0 or not ordered:
                    raise ValueError("cursor does not match an ordered query")
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError("invalid cursor; retain the same ordered SQL, parameters and max_rows") from exc
        page = await sql_gateway.execute_select_page(
            sql,
            parameters=parameters,
            max_rows=max_rows,
            pre_race_mode=effective_pre_race_mode,
            offset=offset,
        )
        next_cursor = None
        if page.truncated and ordered:
            next_cursor = base64.urlsafe_b64encode(
                json.dumps({"query": identity, "offset": offset + len(page.rows)}).encode()
            ).decode()
        note = (
            "一意なORDER BYが必要。ページ間のDB更新による重複/欠落は保証しない。"
            if ordered
            else "ORDER BYがないため継続カーソルを発行しない。一意な並び順を指定して再取得する。"
        )
        return SqlExecuteCustomQueryOutput(
            rows=page.rows,
            requested_limit=max_rows,
            effective_limit=page.effective_limit,
            returned_count=len(page.rows),
            truncated=page.truncated,
            next_cursor=next_cursor,
            pagination_note=note,
        )

    return sql_execute_custom_query
