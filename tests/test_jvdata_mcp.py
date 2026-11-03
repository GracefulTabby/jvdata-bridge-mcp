"""最小限のMCP公開面と依存境界を検証する。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from jvdata_bridge_mcp.config import JvDataSettings
from jvdata_bridge_mcp.database import JvDataDatabase, JvDataSchemaError, quote_identifier
from jvdata_bridge_mcp.server import SERVER_NAME, create_server
from jvdata_bridge_mcp.tools.all import CANONICAL_TOOL_NAMES


def test_settings_read_only_environment_prefix() -> None:
    """設定は専用prefixだけを読む。"""

    settings = JvDataSettings.from_env(
        {
            "DATABASE_URL": "postgresql://unrelated.example/jvdata",
            "JVDATA_BRIDGE_MCP_DATABASE_URL": "postgresql://reader@example/jvdata",
            "JVDATA_BRIDGE_MCP_MAX_ROWS": "321",
        }
    )

    assert settings.database_url == "postgresql://reader@example/jvdata"
    assert settings.max_rows == 321


def test_settings_default_command_timeout_is_sixty_seconds() -> None:
    """長い分析を許容する既定SQLタイムアウトと接続タイムアウトを区別する。"""

    settings = JvDataSettings.from_env({})

    assert settings.command_timeout_seconds == 60
    assert settings.connect_timeout_seconds == 5


def test_identifier_guard_rejects_sql_injection_text() -> None:
    """動的識別子はPostgreSQLの単純な名前だけを受け付ける。"""

    assert quote_identifier("race_cards") == '"race_cards"'
    try:
        quote_identifier('race_cards"; DROP TABLE races; --')
    except JvDataSchemaError:
        pass
    else:
        raise AssertionError("unsafe identifier was accepted")


def test_relation_columns_awaits_execute_before_reading_scalars(monkeypatch: pytest.MonkeyPatch) -> None:
    """metadata queryの非同期実行結果を得てから列名を読む。"""

    class FakeScalars:
        def all(self) -> list[str]:
            return ["race_id", "horse_number"]

    class FakeResult:
        def scalars(self) -> FakeScalars:
            return FakeScalars()

    class FakeConnection:
        async def execute(self, _statement: object, parameters: dict[str, str]) -> FakeResult:
            assert parameters == {"schema": "jv_mart", "relation": "race_cards"}
            return FakeResult()

    database = JvDataDatabase(JvDataSettings(database_url="postgresql://reader@invalid/jvdata"))

    @asynccontextmanager
    async def fake_read_connection(*, statement_timeout_seconds: int | None = None) -> object:
        assert statement_timeout_seconds is None
        yield FakeConnection()

    monkeypatch.setattr(database, "read_connection", fake_read_connection)

    assert asyncio.run(database.relation_columns("jv_mart", "race_cards")) == (
        "race_id",
        "horse_number",
    )


def test_server_exposes_the_canonical_public_surface() -> None:
    """tools/listは単一のcanonical 50ツールを公開する。"""

    database = JvDataDatabase(JvDataSettings(database_url="postgresql://reader@invalid/jvdata"))
    server = create_server(database)
    tools = asyncio.run(server.list_tools())

    assert server.name == SERVER_NAME
    names = [tool.name for tool in tools]
    assert len(names) == 50
    assert names == list(CANONICAL_TOOL_NAMES)
    assert len(names) == len(set(names))
    assert names[:3] == ["race_summary", "entries_list", "track_condition_summary"]
    assert tools[0].inputSchema["properties"] == {
        "race_code": {
            "description": "対象レースを識別する16桁のレースコード",
            "title": "Race Code",
            "type": "string",
        }
    }
    assert tools[0].outputSchema is not None
    assert "data" not in tools[0].outputSchema.get("properties", {})
    for tool in tools:
        assert tool.outputSchema is not None
        assert tool.outputSchema.get("type") == "object"
        assert "data" not in tool.outputSchema.get("properties", {})

    entries_list = next(tool for tool in tools if tool.name == "entries_list")
    assert entries_list.outputSchema is not None
    assert entries_list.outputSchema["properties"]["entries"]["type"] == "array"
    entry_ref = entries_list.outputSchema["properties"]["entries"]["items"]["$ref"]
    assert entries_list.outputSchema["$defs"][entry_ref.rsplit("/", 1)[-1]]["additionalProperties"] is False
    for tool in tools:
        assert "as_of_datetime" not in tool.inputSchema.get("properties", {})
        assert "as_of_date" not in tool.inputSchema.get("properties", {})
        assert "scope" not in tool.inputSchema.get("properties", {})
        assert "lead_minutes" not in tool.inputSchema.get("properties", {})
    assert not {
        "race_today_forecast_context",
        "race_tomorrow_forecast_context",
        "odds_tansho_table",
        "odds_fukusho_table",
        "odds_fetch_race_summary",
        "training_pattern_match",
        "track_bias_analysis",
    } & set(names)
    assert {"course_frame_style_stats", "prediction_model_status"} <= set(names)
    probabilities = next(tool for tool in tools if tool.name == "predict_race_probabilities")
    assert set(probabilities.inputSchema["properties"]) == {"race_code", "combinations"}
    assert probabilities.inputSchema["required"] == ["race_code"]


def test_server_import_does_not_load_external_application() -> None:
    """公開サーバーは別アプリケーションのモジュールimportに依存しない。"""

    repository_root = Path(__file__).resolve().parents[1]
    code = """
import asyncio
import sys

from jvdata_bridge_mcp.config import JvDataSettings
from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.server import create_server

server = create_server(JvDataDatabase(JvDataSettings(database_url='postgresql://reader@invalid/jvdata')))
tools = asyncio.run(server.list_tools())
blocked_prefixes = ('apps', 'src.')
blocked = sorted(name for name in sys.modules if name.startswith(blocked_prefixes))
if len(tools) != 50 or blocked:
    raise SystemExit(f'bad import boundary: tools={len(tools)} blocked={blocked}')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
