"""ツール入力の正規化と安全制約を検証する。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from jvdata_bridge_mcp.tools.bloodline import form_profile as bloodline_form_profile
from jvdata_bridge_mcp.tools.bloodline import pedigree as bloodline_pedigree
from jvdata_bridge_mcp.tools.horse import compare as horse_compare
from jvdata_bridge_mcp.tools.horse import details as horse_details
from jvdata_bridge_mcp.tools.jockey import profile as jockey_profile
from jvdata_bridge_mcp.tools.misc import custom_sql
from jvdata_bridge_mcp.tools.training import entry_detail as training_entry_detail

RACE_CODE = "2026082307030212"


def _run(tool: Any, *args: object, **kwargs: object) -> object:
    """非同期のtool関数を同期テストから呼び出す。"""

    return asyncio.run(tool(*args, **kwargs))


@pytest.mark.parametrize(
    ("module", "builder", "output_attribute", "horse_number_sql"),
    [
        (
            bloodline_pedigree,
            bloodline_pedigree.build_bloodline_fetch_pedigree_tool,
            "pedigrees",
            "c.horse_number = :horse_number",
        ),
        (
            bloodline_form_profile,
            bloodline_form_profile.build_bloodline_form_profile_tool,
            "profiles",
            "horse_number = :horse_number",
        ),
        (
            training_entry_detail,
            training_entry_detail.build_training_entry_detail_tool,
            "recent_history",
            "c.horse_number = :horse_number",
        ),
    ],
)
def test_padded_horse_number_is_normalized_at_the_sql_boundary(
    module: Any,
    builder: Any,
    output_attribute: str,
    horse_number_sql: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ゼロ埋めした馬番01をSQL照会用の整数へ正規化する。"""

    calls: list[dict[str, object]] = []

    async def fake_fetch_rows(
        database: object,
        statement_sql: str,
        parameters: Mapping[str, object] | None = None,
        **_: object,
    ) -> list[dict[str, object]]:
        del database
        calls.append({"sql": statement_sql, "parameters": dict(parameters or {})})
        return []

    monkeypatch.setattr(module, "fetch_rows", fake_fetch_rows)
    tool = builder(object())

    result = _run(tool, "01", RACE_CODE)

    assert getattr(result, output_attribute) == []
    assert calls[0]["parameters"] == {
        "horse_number": 1,
        "race_code": RACE_CODE,
        **({"recent_runs": 5} if output_attribute == "profiles" else {}),
        "limit": {"pedigrees": 1_000, "profiles": 500, "recent_history": 100}[output_attribute],
    }
    assert horse_number_sql in str(calls[0]["sql"])
    assert "CAST(c.horse_number" not in str(calls[0]["sql"])


@pytest.mark.parametrize(
    "horse_number",
    ["", "0", "00", "19", "1a", "1.0", "１", " 01 ", "-1"],
)
@pytest.mark.parametrize(
    ("module", "builder"),
    [
        (bloodline_pedigree, bloodline_pedigree.build_bloodline_fetch_pedigree_tool),
        (bloodline_form_profile, bloodline_form_profile.build_bloodline_form_profile_tool),
        (training_entry_detail, training_entry_detail.build_training_entry_detail_tool),
    ],
)
def test_horse_number_rejects_invalid_values_before_database_access(
    horse_number: str,
    module: Any,
    builder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """数値でない馬番や競馬の範囲外を空結果として隠さない。"""

    called = False

    async def fake_fetch_rows(
        database: object,
        statement_sql: str,
        parameters: Mapping[str, object] | None = None,
        **_: object,
    ) -> list[dict[str, object]]:
        del database, statement_sql, parameters
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(module, "fetch_rows", fake_fetch_rows)
    tool = builder(object())

    with pytest.raises(ValueError, match="horse_number"):
        _run(tool, horse_number, RACE_CODE)
    assert called is False


class _RecordingSqlGateway:
    """custom SQLの実効pre_race_modeを記録するfake gateway。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute_select_page(
        self, sql: str, *, parameters: Mapping[str, object], max_rows: int, pre_race_mode: bool, offset: int = 0
    ) -> object:
        from jvdata_bridge_mcp.sql_gateway import SqlPage

        rows = await self.execute_select(sql, parameters=parameters, max_rows=max_rows, pre_race_mode=pre_race_mode)
        return SqlPage(rows, min(max_rows, 200), False)

    async def execute_select(
        self,
        sql: str,
        *,
        parameters: Mapping[str, object],
        max_rows: int,
        pre_race_mode: bool,
    ) -> list[dict[str, object]]:
        self.calls.append(
            {
                "sql": sql,
                "parameters": dict(parameters),
                "max_rows": max_rows,
                "pre_race_mode": pre_race_mode,
            }
        )
        return [{"probe": 1}]


@pytest.mark.parametrize(
    ("context", "pre_race_mode", "expected_effective_mode"),
    [
        (None, False, False),
        ("development", False, False),
        ("pre_race_prediction", False, True),
        ("pre_race_prediction", True, True),
        ("post_race_review", False, False),
        ("post_race_review", True, True),
    ],
)
def test_sql_context_enforces_pre_race_access(
    context: str | None,
    pre_race_mode: bool,
    expected_effective_mode: bool,
) -> None:
    """対応する実行コンテキストを受理し、予測前の参照制約を適用する。"""

    gateway = _RecordingSqlGateway()
    tool = custom_sql.build_sql_execute_custom_query_tool(object(), gateway)

    result = _run(tool, "SELECT 1", context=context, pre_race_mode=pre_race_mode)

    assert getattr(result, "rows") == [{"probe": 1}]
    assert gateway.calls[-1]["pre_race_mode"] is expected_effective_mode


def test_sql_context_rejects_unknown_values_before_gateway_access() -> None:
    """未知contextをdevelopment扱いして安全制約を迂回させない。"""

    gateway = _RecordingSqlGateway()
    tool = custom_sql.build_sql_execute_custom_query_tool(object(), gateway)

    with pytest.raises(ValueError, match="unsupported context"):
        _run(tool, "SELECT 1", context="unknown", pre_race_mode=False)
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("module", "builder", "empty_kwargs", "valid_kwargs"),
    [
        (
            horse_details,
            horse_details.build_get_horse_details_tool,
            {},
            {"horse_name": "ロード"},
        ),
        (
            jockey_profile,
            jockey_profile.build_get_jockey_profile_tool,
            {},
            {"jockey_code": "01208"},
        ),
    ],
)
def test_profile_tools_require_a_selector(
    module: Any,
    builder: Any,
    empty_kwargs: dict[str, object],
    valid_kwargs: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """selectorなしで全件近いbulk取得を実行しない。"""

    calls: list[dict[str, object]] = []

    async def fake_fetch_rows(
        database: object,
        statement_sql: str,
        parameters: Mapping[str, object] | None = None,
        **_: object,
    ) -> list[dict[str, object]]:
        del database, statement_sql
        calls.append(dict(parameters or {}))
        return []

    monkeypatch.setattr(module, "fetch_rows", fake_fetch_rows)
    tool = builder(object())

    with pytest.raises(ValueError, match="required"):
        _run(tool, **empty_kwargs)
    with pytest.raises(ValueError, match="required"):
        if module is horse_details:
            _run(tool, horse_name="   ", ketto_toroku_bango=" \t")
        else:
            _run(tool, jockey_code="   ", jockey_name=" \t")
    assert calls == []

    _run(tool, **valid_kwargs)
    assert len(calls) == 1


@pytest.mark.parametrize("horse_names", ["", "   ", ",、 \t"])
def test_compare_horses_rejects_an_empty_selector_list(
    horse_names: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空の比較対象を空配列として成功扱いしない。"""

    called = False

    async def fake_fetch_rows(
        database: object,
        statement_sql: str,
        parameters: Mapping[str, object] | None = None,
        **_: object,
    ) -> list[dict[str, object]]:
        del database, statement_sql, parameters
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(horse_compare, "fetch_rows", fake_fetch_rows)
    tool = horse_compare.build_compare_horses_tool(object())

    with pytest.raises(ValueError, match="horse_names"):
        _run(tool, horse_names)
    assert called is False


def test_compare_horses_keeps_id_based_same_name_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    """有効なID指定はtrimだけ行い、同名馬のID優先SQLを維持する。"""

    calls: list[dict[str, object]] = []

    async def fake_fetch_rows(
        database: object,
        statement_sql: str,
        parameters: Mapping[str, object] | None = None,
        **_: object,
    ) -> list[dict[str, object]]:
        del database, statement_sql
        calls.append(dict(parameters or {}))
        return []

    monkeypatch.setattr(horse_compare, "fetch_rows", fake_fetch_rows)
    tool = horse_compare.build_compare_horses_tool(object())

    _run(tool, " 2023105816 ")

    assert calls == [{"horse_names": "2023105816", "limit": 100}]
