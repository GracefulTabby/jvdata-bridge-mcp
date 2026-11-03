"""jvdata read-only SQLゲートウェイのAST安全性試験。"""

from __future__ import annotations

import pytest

from jvdata_bridge_mcp.sql_gateway import JvDataUnsafeQueryError, validate_custom_select

ALLOWED_SCHEMAS: frozenset[str] = frozenset({"jv_data", "jv_mart", "jv_ref"})


def _validate(sql: str, *, pre_race_mode: bool = False) -> str:
    """標準allowlistでSQLを検証する。"""

    return validate_custom_select(
        sql,
        allowed_schemas=ALLOWED_SCHEMAS,
        pre_race_mode=pre_race_mode,
    )


def test_select_and_chained_ctes_are_allowed() -> None:
    """許可schemaを読む複数CTEと末尾セミコロンを受理する。"""

    sql: str = """
        WITH base AS (
            SELECT race_id FROM jv_data.races
        ), recent AS (
            SELECT race_id FROM base
        )
        SELECT race_id FROM recent;
    """

    assert _validate(sql).rstrip().endswith("FROM recent")


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE jv_data.races SET race_name = 'x'",
        "WITH changed AS (DELETE FROM jv_data.races RETURNING *) SELECT * FROM changed",
        "SELECT race_id INTO copied_races FROM jv_data.races",
        "SELECT pg_sleep(1) FROM jv_data.races",
        "SELECT * FROM information_schema.columns",
        "SELECT * FROM public.races",
        "SELECT * FROM races",
        "SELECT * FROM jv_data.races; SELECT * FROM jv_data.races",
        "SELECT * FROM jv_data.races -- hidden text",
        "SELECT * FROM jv_data.races WHERE race_id = $1",
        "SELECT * FROM jv_data.races WHERE race_id = ?",
    ],
)
def test_unsafe_sql_is_rejected(sql: str) -> None:
    """書込み、副作用、カタログ、許可外・未修飾relation、複数文を拒否する。"""

    with pytest.raises(JvDataUnsafeQueryError):
        _validate(sql)


def test_expensive_odds_requires_race_or_bounded_date() -> None:
    """重いオッズread modelは全走査と片側日付条件を拒否する。"""

    with pytest.raises(JvDataUnsafeQueryError):
        _validate("SELECT * FROM jv_mart.latest_odds")
    with pytest.raises(JvDataUnsafeQueryError):
        _validate("SELECT * FROM jv_mart.odds_history WHERE race_date >= '2026-08-01'")

    assert ":race_id" in _validate("SELECT * FROM jv_mart.latest_odds WHERE race_id = :race_id")
    assert _validate("SELECT * FROM jv_mart.latest_odds WHERE race_id = '202608090712'")
    assert _validate("SELECT * FROM jv_mart.odds_history WHERE race_date >= '2026-08-01' AND race_date < '2026-08-08'")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*), SUM(distance), AVG(distance), MIN(distance), MAX(distance) FROM jv_data.races",
        "SELECT LOWER(race_name), UPPER(race_name), TRIM(race_name), SUBSTRING(race_name FROM 1 FOR 3), "
        "CONCAT(race_name, ' race') FROM jv_data.races",
        "SELECT CURRENT_DATE, DATE_TRUNC('day', race_date), EXTRACT(YEAR FROM race_date), "
        "TO_CHAR(race_date, 'YYYY-MM-DD') FROM jv_data.races",
    ],
)
def test_safe_aggregate_string_and_date_functions_are_allowlisted(sql: str) -> None:
    """分析に必要な集計・文字列・日付関数は明示的allowlistで受理する。"""

    assert _validate(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT my_function(race_id) FROM jv_data.races",
        "SELECT public.my_function(race_id) FROM jv_data.races",
        "SELECT jv_data.my_function(race_id) FROM jv_data.races",
        "SELECT pg_catalog.lower(race_name) FROM jv_data.races",
        "SELECT pg_read_file('/tmp/secret') FROM jv_data.races",
        "SELECT pg_ls_dir('.') FROM jv_data.races",
        "SELECT pg_terminate_backend(123) FROM jv_data.races",
        "SELECT set_config('search_path', 'public', false) FROM jv_data.races",
        "SELECT current_setting('search_path') FROM jv_data.races",
        "SELECT * FROM jv_data.generate_series(1, 2)",
    ],
)
def test_unknown_qualified_and_side_effect_functions_are_rejected(sql: str) -> None:
    """UDF、schema修飾、ファイル・backend操作をallowlist外として拒否する。"""

    with pytest.raises(JvDataUnsafeQueryError):
        _validate(sql)


def test_pre_race_mode_rejects_post_race_relations() -> None:
    """予測前モードでは確定結果と事後オッズを参照させない。"""

    with pytest.raises(JvDataUnsafeQueryError):
        _validate("SELECT * FROM jv_mart.horse_runs", pre_race_mode=True)
    with pytest.raises(JvDataUnsafeQueryError):
        _validate(
            "SELECT * FROM jv_mart.latest_odds WHERE race_id = :race_id",
            pre_race_mode=True,
        )

    assert _validate("SELECT * FROM jv_mart.race_cards", pre_race_mode=True)
