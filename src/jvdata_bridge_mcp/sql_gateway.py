"""AST検証付きの読み取り専用SQLゲートウェイとメタデータアクセス。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Mapping, Protocol

from sqlalchemy import bindparam, text
from sqlalchemy.sql.elements import TextClause
from sqlglot import exp, parse
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope

from jvdata_bridge_mcp.config import DATA_SCHEMA, MART_SCHEMA, REF_SCHEMA
from jvdata_bridge_mcp.database import JvDataDatabase, quote_identifier

RowData = dict[str, object]


class JvDataUnsafeQueryError(ValueError):
    """無条件の重い走査またはallowlist外クエリを拒否した場合に送出する。"""


_POST_RACE_RELATIONS: frozenset[str] = frozenset(
    {
        "race_results",
        "horse_runs",
        "payouts",
        "latest_odds",
        "odds_history",
        "vote_counts",
        "course_records",
        "record_holders",
    }
)
_EXPENSIVE_RELATIONS: frozenset[str] = frozenset({"latest_odds", "odds_history"})
_FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {
        "dblink",
        "lo_export",
        "lo_import",
        "nextval",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "pg_cancel_backend",
        "pg_ls_dir",
        "pg_read_binary_file",
        "pg_read_file",
        "pg_reload_conf",
        "pg_sleep",
        "pg_stat_file",
        "pg_terminate_backend",
        "reset_config",
        "set_config",
        "setval",
    }
)
# Functions are deliberately allowlisted instead of trying to enumerate every
# dangerous PostgreSQL function.  An unqualified function name is resolved with
# pg_catalog first by PostgreSQL; execute_select also sets search_path to
# pg_catalog so a user-defined function cannot shadow one of these names.
_ALLOWED_ANONYMOUS_FUNCTIONS: frozenset[str] = frozenset(
    {
        "age",
        "btrim",
        "every",
        "jsonb_agg",
        "make_date",
        "octet_length",
        "trunc",
    }
)
_ALLOWED_FUNCTION_CLASSES: frozenset[str] = frozenset(
    {
        # Numeric and conditional scalar functions.
        "Abs",
        "Ceil",
        "Coalesce",
        "Date",
        "Floor",
        "Greatest",
        "Least",
        "Mod",
        "Nullif",
        "Pow",
        "Round",
        "Sign",
        "Sqrt",
        # Aggregates.
        "ArrayAgg",
        "Avg",
        "Count",
        "GroupConcat",  # PostgreSQL STRING_AGG
        "JSONArrayAgg",  # PostgreSQL JSON_AGG
        "LogicalAnd",  # PostgreSQL BOOL_AND
        "LogicalOr",  # PostgreSQL BOOL_OR
        "Max",
        "Min",
        "Stddev",
        "StddevPop",
        "StddevSamp",
        "Sum",
        "Variance",
        "VariancePop",
        # String functions.
        "Ascii",
        "BitLength",
        "Char",
        "Chr",
        "Concat",
        "ConcatWs",
        "Initcap",
        "Left",
        "Length",
        "Lower",
        "LPad",
        "Pad",
        "Position",
        "Replace",
        "Repeat",
        "RegexpReplace",
        "Reverse",
        "Right",
        "RPad",
        "SplitPart",
        "StrPosition",
        "Substring",
        "Translate",
        "Trim",
        "Upper",
        # Date/time functions and conversions.
        "CurrentDate",
        "CurrentTime",
        "CurrentTimestamp",
        "DateAdd",
        "DateBin",
        "DateDiff",
        "DateSub",
        "DateTrunc",
        "Extract",
        "MakeInterval",
        "StrToDate",
        "StrToTime",
        "TimeToStr",
        "TimestampTrunc",  # PostgreSQL DATE_TRUNC
        # Explicit type conversion is not a function lookup and has no side effect.
        "Cast",
        "TryCast",
        # Safe window functions.
        "CumeDist",
        "DenseRank",
        "FirstValue",
        "Lag",
        "LastValue",
        "Lead",
        "NthValue",
        "Ntile",
        "PercentRank",
        "Rank",
        "RowNumber",
    }
)
_FORBIDDEN_EXPRESSIONS: tuple[type[exp.Expression], ...] = (
    exp.Alter,
    exp.Command,
    exp.Copy,
    exp.Create,
    exp.Delete,
    exp.Drop,
    exp.Insert,
    exp.Merge,
    exp.Update,
    exp.Grant,
    exp.Revoke,
    exp.TruncateTable,
)
_BIND_PARAMETER_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PYFORMAT_PARAMETER_PATTERN: re.Pattern[str] = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")


def _parse_read_query(sql: str) -> tuple[str, exp.Expression]:
    """sqlglotでPostgreSQLの読み取りクエリを1文だけ解析する。"""

    normalized = sql.strip()
    if not normalized:
        raise JvDataUnsafeQueryError("sql must not be empty")
    if "--" in normalized or "/*" in normalized or "*/" in normalized:
        raise JvDataUnsafeQueryError("SQL comments are not allowed")
    try:
        statements = parse(normalized, read="postgres")
    except ParseError as exc:
        raise JvDataUnsafeQueryError("SQL could not be parsed as PostgreSQL") from exc
    if len(statements) != 1:
        raise JvDataUnsafeQueryError("only one SQL statement is allowed")
    expression = statements[0]
    if not isinstance(expression, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise JvDataUnsafeQueryError("only SELECT or WITH statements are allowed")
    if expression.find(exp.Parameter) is not None:
        raise JvDataUnsafeQueryError("use named :parameter placeholders with parameters_json")
    for placeholder in expression.find_all(exp.Placeholder):
        if not _BIND_PARAMETER_PATTERN.fullmatch(placeholder.name):
            raise JvDataUnsafeQueryError("bind parameter names must be simple identifiers")
    # PostgreSQL dialectで再生成してDATE_TRUNCやSTRING_AGGなどの関数を
    # PostgreSQLの構文のまま保つ。SQLAlchemyのtext()へ渡すため、生成器が
    # psycopg形式へ変換した名前付きplaceholderだけを元へ戻す。
    normalized = expression.sql(dialect="postgres")
    return _PYFORMAT_PARAMETER_PATTERN.sub(r":\1", normalized), expression


def _cte_names(expression: exp.Expression) -> frozenset[str]:
    """スキーマなしで参照できるCTE名を返す。"""

    return frozenset(cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE) if cte.alias_or_name)


def _validate_ast(
    expression: exp.Expression,
    *,
    allowed_schemas: frozenset[str],
    pre_race_mode: bool,
) -> frozenset[str]:
    """文、リレーション、関数、時系列に関する制約を検証する。"""

    for node in expression.walk():
        if isinstance(node, _FORBIDDEN_EXPRESSIONS):
            raise JvDataUnsafeQueryError(f"SQL statement is not read-only: {type(node).__name__}")
    if expression.find(exp.Into) is not None:
        raise JvDataUnsafeQueryError("SELECT INTO is not allowed")
    for dot in expression.find_all(exp.Dot):
        if isinstance(dot.expression, (exp.Func, exp.UserDefinedFunction)):
            raise JvDataUnsafeQueryError(f"schema-qualified SQL functions are not allowed: {dot.sql()}")
    for function in expression.find_all(exp.UserDefinedFunction):
        raise JvDataUnsafeQueryError(f"user-defined SQL function is not allowed: {function.sql()}")
    for function in expression.find_all(exp.Func):
        if isinstance(function, exp.Anonymous):
            function_name = function.name.lower()
            if function_name in _FORBIDDEN_FUNCTIONS:
                raise JvDataUnsafeQueryError(f"SQL function is not allowed: {function.name}")
            if function_name not in _ALLOWED_ANONYMOUS_FUNCTIONS:
                raise JvDataUnsafeQueryError(f"SQL function is not in the allowlist: {function.name}")
        elif isinstance(function, (exp.Binary, exp.Predicate, exp.Unary, exp.Case, exp.If)):
            # sqlglot derives boolean operators, predicates, CASE and unary
            # expressions from Func as an implementation detail. They are SQL
            # expressions, not callable functions, so they are not part of the
            # function allowlist.
            continue
        elif type(function).__name__ not in _ALLOWED_FUNCTION_CLASSES:
            raise JvDataUnsafeQueryError(f"SQL function is not in the allowlist: {type(function).__name__}")
    if expression.find(exp.Lock) is not None:
        raise JvDataUnsafeQueryError("row locking clauses are not allowed")

    ctes = _cte_names(expression)
    relations: list[str] = []
    for table in expression.find_all(exp.Table):
        schema = table.db.lower()
        name = table.name.lower()
        if not schema and name in ctes:
            continue
        if not schema:
            raise JvDataUnsafeQueryError("FROM and JOIN relations must use an allowed schema prefix")
        if table.catalog:
            raise JvDataUnsafeQueryError("catalog-qualified relations are not allowed")
        if schema not in allowed_schemas:
            raise JvDataUnsafeQueryError(f"SQL references a forbidden schema: {schema}")
        relations.append(name)
    if not relations:
        raise JvDataUnsafeQueryError("custom SQL must reference at least one allowed read model")

    relation_names = frozenset(relations)
    if pre_race_mode and relation_names & _POST_RACE_RELATIONS:
        raise JvDataUnsafeQueryError("pre_race_mode forbids post-race read models")
    expensive = relation_names & _EXPENSIVE_RELATIONS
    if expensive and not _has_bounded_expensive_filter(expression):
        raise JvDataUnsafeQueryError("latest_odds/odds_history requires race_id equality or a bounded race_date range")
    return relation_names


def _constant_bound(node: exp.Expression) -> bool:
    """リテラル/バインドの境界だけを認め、列間結合を境界と数えない。"""

    while isinstance(node, (exp.Cast, exp.Paren)):
        node = node.this
    return isinstance(node, (exp.Literal, exp.Placeholder))


def _bound_bits(node: exp.Expression, alias: str, *, unqualified: bool) -> int:
    """対象relationのrace_id等価=1、日付下限=2、上限=4を返す。"""

    if not isinstance(node, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return 0
    for column, value, reversed_sides in ((node.left, node.right, False), (node.right, node.left, True)):
        if not isinstance(column, exp.Column) or not _constant_bound(value):
            continue
        if column.table.lower() != alias and not (not column.table and unqualified):
            continue
        if column.name.lower() == "race_id" and isinstance(node, exp.EQ):
            return 1
        if column.name.lower() == "race_date" and not isinstance(node, exp.EQ):
            lower = isinstance(node, (exp.GT, exp.GTE)) != reversed_sides
            return 2 if lower else 4
    return 0


def _predicate_branches(node: exp.Expression) -> list[list[exp.Expression]]:
    """ORの全枝を検証できるようAND/ORを展開する（過大な式は拒否）。"""

    if isinstance(node, exp.Paren):
        return _predicate_branches(node.this)
    if isinstance(node, exp.Or):
        branches = _predicate_branches(node.left) + _predicate_branches(node.right)
    elif isinstance(node, exp.And):
        branches = [a + b for a in _predicate_branches(node.left) for b in _predicate_branches(node.right)]
    else:
        return [[node]]
    if len(branches) > 32:
        raise JvDataUnsafeQueryError("odds filter has too many OR branches; use bounded CTEs")
    return branches


def _has_bounded_expensive_filter(expression: exp.Expression) -> bool:
    """各SELECT内の各重いrelationについて全OR枝の境界を要求する。"""

    for scope in traverse_scope(expression):
        sources = scope.selected_sources
        for alias, (_, source) in sources.items():
            if not isinstance(source, exp.Table) or source.name.lower() not in _EXPENSIVE_RELATIONS:
                continue
            where = scope.expression.args.get("where")
            if where is None:
                return False
            for branch in _predicate_branches(where.this):
                bits = 0
                for atom in branch:
                    bits |= _bound_bits(atom, alias.lower(), unqualified=len(sources) == 1)
                if not (bits & 1 or bits & 6 == 6):
                    return False
    return True


@dataclass(frozen=True)
class SqlPage:
    """外側上限の一行先を確認したページ。"""

    rows: list[RowData]
    effective_limit: int
    truncated: bool


def validate_custom_select(
    sql: str,
    *,
    allowed_schemas: frozenset[str],
    pre_race_mode: bool,
) -> str:
    """公開SQLを単一SELECT/CTE・許可schemaへ制限する。"""

    normalized, expression = _parse_read_query(sql)
    _validate_ast(
        expression,
        allowed_schemas=allowed_schemas,
        pre_race_mode=pre_race_mode,
    )
    return normalized


def _safe_value(value: object) -> object:
    """DB値をMCP構造化出力でJSON化できる値へ変換する。"""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    return str(value)


class JvDataSqlGateway:
    """テキストSQLとinformation_schema参照を安全に実行する。"""

    def __init__(self, database: JvDataDatabase) -> None:
        self.database: JvDataDatabase = database
        self.allowed_schemas: frozenset[str] = frozenset({DATA_SCHEMA, MART_SCHEMA, REF_SCHEMA})

    def bounded_row_limit(self, requested: int) -> int:
        """公開SQLの行数上限を設定値と絶対上限へ丸める。"""

        return max(1, min(requested, self.database.settings.max_rows, 1_000))

    async def execute_select(
        self,
        sql: str,
        *,
        parameters: Mapping[str, object],
        max_rows: int,
        pre_race_mode: bool,
    ) -> list[RowData]:
        """検査済みSQLをread-only transaction内で外側LIMIT付き実行する。"""

        return await self._execute_rows(
            sql, parameters=parameters, limit=self.bounded_row_limit(max_rows), offset=0, pre_race_mode=pre_race_mode
        )

    async def execute_select_page(
        self,
        sql: str,
        *,
        parameters: Mapping[str, object],
        max_rows: int,
        pre_race_mode: bool,
        offset: int = 0,
    ) -> SqlPage:
        """上限+1行を読み、次の行の存在を通知する。"""

        limit = self.bounded_row_limit(max_rows)
        rows = await self._execute_rows(
            sql, parameters=parameters, limit=limit + 1, offset=offset, pre_race_mode=pre_race_mode
        )
        return SqlPage(rows=rows[:limit], effective_limit=limit, truncated=len(rows) > limit)

    async def _execute_rows(
        self,
        sql: str,
        *,
        parameters: Mapping[str, object],
        limit: int,
        offset: int,
        pre_race_mode: bool,
    ) -> list[RowData]:
        """公開経路の検証を維持して内部ページを実行する。"""

        if offset < 0:
            raise JvDataUnsafeQueryError("page offset must be nonnegative")
        normalized: str = validate_custom_select(
            sql,
            allowed_schemas=self.allowed_schemas,
            pre_race_mode=pre_race_mode,
        )
        wrapped_sql = (
            f"SELECT * FROM ({normalized}) AS jv_custom_query LIMIT :jv_custom_limit OFFSET :jv_custom_offset"
        )
        statement: TextClause = text(wrapped_sql)
        payload: dict[str, object] = dict(parameters)
        if {"jv_custom_limit", "jv_custom_offset"} & payload.keys():
            raise JvDataUnsafeQueryError("reserved parameter name: jv_custom_limit/jv_custom_offset")
        payload["jv_custom_limit"] = limit
        payload["jv_custom_offset"] = offset
        async with self.database.read_connection(
            statement_timeout_seconds=self.database.settings.command_timeout_seconds
        ) as connection:
            # 固定スキーマのread modelだけを参照するcustom SQLでは、ユーザー側の
            # search_pathにある同名UDFがallowlisted builtinをshadowできないようにする。
            await connection.execute(text("SET LOCAL search_path TO pg_catalog"))
            rows = (await connection.execute(statement, payload)).mappings().all()
        return [{str(key): _safe_value(value) for key, value in row.items()} for row in rows]

    async def list_tables(self) -> list[RowData]:
        """許可schemaのテーブル・ビュー一覧を返す。"""

        statement = text(
            """
            SELECT
                n.nspname AS table_schema,
                c.relname AS relation_name,
                c.relkind AS relation_kind,
                COALESCE(obj_description(c.oid, 'pg_class'), '') AS description
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname IN :schemas
              AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
            ORDER BY n.nspname, c.relname
            """
        ).bindparams(bindparam("schemas", expanding=True))
        async with self.database.read_connection() as connection:
            result = await connection.execute(
                statement,
                {"schemas": tuple(sorted(self.allowed_schemas))},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def _resolve_relation(self, table_name: str) -> tuple[str, str]:
        """schema付きまたは一意なrelation名を許可schema内で解決する。"""

        normalized: str = table_name.strip().lower()
        parts: list[str] = normalized.split(".")
        if len(parts) == 2:
            schema, relation = parts
            quote_identifier(schema)
            quote_identifier(relation)
            if schema not in self.allowed_schemas:
                raise JvDataUnsafeQueryError(f"schema is not allowed: {schema}")
            return schema, relation
        if len(parts) != 1:
            raise JvDataUnsafeQueryError("table_name must be relation or schema.relation")
        relation = parts[0]
        quote_identifier(relation)
        matches: list[RowData] = [row for row in await self.list_tables() if str(row.get("relation_name")) == relation]
        if len(matches) != 1:
            raise JvDataUnsafeQueryError("unqualified table_name must resolve to exactly one allowed relation")
        return str(matches[0]["table_schema"]), relation

    async def table_schema(self, table_name: str) -> RowData:
        """列、主キー、外部キー、索引をまとめて返す。"""

        schema, relation = await self._resolve_relation(table_name)
        column_statement = text(
            """
            SELECT
                c.column_name,
                c.data_type,
                c.udt_name,
                c.is_nullable,
                c.column_default,
                c.ordinal_position,
                COALESCE(pd.description, '') AS comment,
                COALESCE(obj_description(pc.oid, 'pg_class'), '') AS table_description
            FROM information_schema.columns AS c
            LEFT JOIN pg_catalog.pg_namespace AS pn
              ON pn.nspname = c.table_schema
            LEFT JOIN pg_catalog.pg_class AS pc
              ON pc.relname = c.table_name AND pc.relnamespace = pn.oid
            LEFT JOIN pg_catalog.pg_attribute AS pa
              ON pa.attrelid = pc.oid AND pa.attname = c.column_name
            LEFT JOIN pg_catalog.pg_description AS pd
              ON pd.objoid = pc.oid AND pd.objsubid = pa.attnum
            WHERE c.table_schema = :schema AND c.table_name = :relation
            ORDER BY c.ordinal_position
            """
        )
        primary_statement = text(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.constraint_schema = kcu.constraint_schema
            WHERE tc.table_schema = :schema
              AND tc.table_name = :relation
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
            """
        )
        foreign_statement = text(
            """
            SELECT
                tc.constraint_name,
                array_agg(kcu.column_name ORDER BY kcu.ordinal_position) AS constrained_columns,
                ccu.table_schema || '.' || ccu.table_name AS referred_table,
                array_agg(ccu.column_name ORDER BY kcu.ordinal_position) AS referred_columns
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.constraint_schema = kcu.constraint_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.constraint_schema = tc.constraint_schema
            WHERE tc.table_schema = :schema
              AND tc.table_name = :relation
              AND tc.constraint_type = 'FOREIGN KEY'
            GROUP BY tc.constraint_name, ccu.table_schema, ccu.table_name
            ORDER BY tc.constraint_name
            """
        )
        index_statement = text(
            """
            SELECT
                indexname AS name,
                indexdef ILIKE '% UNIQUE %' AS is_unique,
                indexdef
            FROM pg_catalog.pg_indexes
            WHERE schemaname = :schema AND tablename = :relation
            ORDER BY indexname
            """
        )
        parameters: dict[str, str] = {"schema": schema, "relation": relation}
        async with self.database.read_connection() as connection:
            columns = (await connection.execute(column_statement, parameters)).mappings().all()
            primary_key = (await connection.execute(primary_statement, parameters)).scalars().all()
            foreign_keys = (await connection.execute(foreign_statement, parameters)).mappings().all()
            indexes = (await connection.execute(index_statement, parameters)).mappings().all()
        if not columns:
            raise JvDataUnsafeQueryError(f"relation was not found: {schema}.{relation}")
        return {
            "table_schema": schema,
            "relation_name": relation,
            "description": str(columns[0].get("table_description") or ""),
            "columns": [dict(row) for row in columns],
            "primary_key": [str(value) for value in primary_key],
            "foreign_keys": [dict(row) for row in foreign_keys],
            "indexes": [dict(row) for row in indexes],
        }

    async def search_columns(self, keyword: str) -> list[RowData]:
        """列名または列コメントを部分一致検索する。"""

        statement = text(
            """
            SELECT
                c.table_schema,
                c.table_name,
                c.column_name,
                c.data_type,
                COALESCE(pd.description, '') AS comment
            FROM information_schema.columns AS c
            LEFT JOIN pg_catalog.pg_namespace AS pn
              ON pn.nspname = c.table_schema
            LEFT JOIN pg_catalog.pg_class AS pc
              ON pc.relname = c.table_name AND pc.relnamespace = pn.oid
            LEFT JOIN pg_catalog.pg_attribute AS pa
              ON pa.attrelid = pc.oid AND pa.attname = c.column_name
            LEFT JOIN pg_catalog.pg_description AS pd
              ON pd.objoid = pc.oid AND pd.objsubid = pa.attnum
            WHERE c.table_schema IN :schemas
              AND (c.column_name ILIKE :keyword OR COALESCE(pd.description, '') ILIKE :keyword)
            ORDER BY c.table_schema, c.table_name, c.ordinal_position
            LIMIT 1000
            """
        ).bindparams(bindparam("schemas", expanding=True))
        async with self.database.read_connection() as connection:
            result = await connection.execute(
                statement,
                {
                    "schemas": tuple(sorted(self.allowed_schemas)),
                    "keyword": f"%{keyword}%",
                },
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def relationships(self) -> list[RowData]:
        """許可schema内の外部キー関係を返す。"""

        statement = text(
            """
            SELECT
                tc.constraint_name,
                tc.table_schema || '.' || tc.table_name AS from_table,
                array_agg(kcu.column_name ORDER BY kcu.ordinal_position) AS from_columns,
                ccu.table_schema || '.' || ccu.table_name AS referred_table,
                array_agg(ccu.column_name ORDER BY kcu.ordinal_position) AS referred_columns
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.constraint_schema = kcu.constraint_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.constraint_schema = tc.constraint_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema IN :schemas
              AND ccu.table_schema IN :referred_schemas
            GROUP BY tc.constraint_name, tc.table_schema, tc.table_name,
                     ccu.table_schema, ccu.table_name
            ORDER BY from_table, tc.constraint_name
            """
        ).bindparams(
            bindparam("schemas", expanding=True),
            bindparam("referred_schemas", expanding=True),
        )
        async with self.database.read_connection() as connection:
            result = await connection.execute(
                statement,
                {
                    "schemas": tuple(sorted(self.allowed_schemas)),
                    "referred_schemas": tuple(sorted(self.allowed_schemas)),
                },
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def columns_by_datatype(self, data_type: str) -> list[RowData]:
        """PostgreSQL data_typeまたはudt_nameで列を検索する。"""

        statement = text(
            """
            SELECT
                c.table_schema,
                c.table_name,
                c.column_name,
                c.data_type,
                c.udt_name,
                COALESCE(pd.description, '') AS comment
            FROM information_schema.columns AS c
            LEFT JOIN pg_catalog.pg_namespace AS pn
              ON pn.nspname = c.table_schema
            LEFT JOIN pg_catalog.pg_class AS pc
              ON pc.relname = c.table_name AND pc.relnamespace = pn.oid
            LEFT JOIN pg_catalog.pg_attribute AS pa
              ON pa.attrelid = pc.oid AND pa.attname = c.column_name
            LEFT JOIN pg_catalog.pg_description AS pd
              ON pd.objoid = pc.oid AND pd.objsubid = pa.attnum
            WHERE c.table_schema IN :schemas
              AND (lower(c.data_type) = lower(:data_type) OR lower(c.udt_name) = lower(:data_type))
            ORDER BY c.table_schema, c.table_name, c.ordinal_position
            LIMIT 1000
            """
        ).bindparams(bindparam("schemas", expanding=True))
        async with self.database.read_connection() as connection:
            result = await connection.execute(
                statement,
                {
                    "schemas": tuple(sorted(self.allowed_schemas)),
                    "data_type": data_type,
                },
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]


class JvDataSqlGatewayProtocol(Protocol):
    """SQL系ツールが利用する読み取り専用ゲートウェイの境界。"""

    def bounded_row_limit(self, requested: int) -> int:
        """公開SQLの実効行数上限を返す。"""

        ...

    async def execute_select(
        self,
        sql: str,
        *,
        parameters: Mapping[str, object],
        max_rows: int,
        pre_race_mode: bool,
    ) -> list[RowData]:
        """検査済みSELECTを実行する。"""

        ...

    async def execute_select_page(
        self,
        sql: str,
        *,
        parameters: Mapping[str, object],
        max_rows: int,
        pre_race_mode: bool,
        offset: int = 0,
    ) -> SqlPage:
        """切り詰めを明示するページを実行する。"""

        ...

    async def list_tables(self) -> list[RowData]:
        """許可schemaのrelation一覧を返す。"""

        ...

    async def table_schema(self, table_name: str) -> RowData:
        """1 relationの列・制約・索引を返す。"""

        ...

    async def search_columns(self, keyword: str) -> list[RowData]:
        """列をキーワード検索する。"""

        ...

    async def relationships(self) -> list[RowData]:
        """外部キー関係を返す。"""

        ...

    async def columns_by_datatype(self, data_type: str) -> list[RowData]:
        """データ型に一致する列を返す。"""

        ...
