"""実際のMCPクライアントからstdioとStreamable HTTPを検証する。"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

REPOSITORY_ROOT: Path = Path(__file__).resolve().parents[1]
SERVER_COMMAND: Path = Path(sys.executable).with_name("jvdata-bridge-mcp")
RACE_CODE: str = "2026080907020612"


def _test_environment(database_url: str | None = None) -> dict[str, str]:
    """接続失敗を短時間で構造化エラーへ変換するテスト環境を作る。"""

    environment: dict[str, str] = {
        key: value for key, value in os.environ.items() if not key.startswith("JVDATA_BRIDGE_MCP_")
    }
    environment.update(
        {
            "JVDATA_BRIDGE_MCP_DATABASE_URL": database_url or "postgresql+psycopg://reader@127.0.0.1:1/jvdata",
            "JVDATA_BRIDGE_MCP_CONNECT_TIMEOUT_SECONDS": "1",
        }
    )
    return environment


async def _assert_client_surface(session: ClientSession) -> None:
    """一覧・呼出し・出力スキーマ検証を共通確認する。"""

    await session.initialize()
    listed = await session.list_tools()
    assert len(listed.tools) == 50
    race_summary = next(tool for tool in listed.tools if tool.name == "race_summary")
    assert set(race_summary.inputSchema["required"]) == {"race_code"}
    assert race_summary.outputSchema is not None
    assert "data" not in race_summary.outputSchema.get("properties", {})
    entries_list = next(tool for tool in listed.tools if tool.name == "entries_list")
    assert entries_list.outputSchema is not None
    assert entries_list.outputSchema.get("type") == "object"
    assert entries_list.outputSchema["properties"]["entries"]["type"] == "array"
    entry_ref = entries_list.outputSchema["properties"]["entries"]["items"]["$ref"]
    assert entries_list.outputSchema["$defs"][entry_ref.rsplit("/", 1)[-1]]["additionalProperties"] is False
    result = await session.call_tool("race_summary", {"race_code": RACE_CODE})
    assert result.isError is True
    assert result.structuredContent in (None, {})
    resources = await session.list_resources()
    assert "jvdata://knowledge/spec/knowledge/horse-evaluation" in {
        str(resource.uri) for resource in resources.resources
    }
    knowledge = await session.read_resource(AnyUrl("jvdata://knowledge/spec/knowledge/horse-evaluation"))
    assert len(knowledge.contents) == 1
    assert "Ability ― 純粋能力" in knowledge.contents[0].text


def _exercise_stdio() -> None:
    """canonical stdio公開面を実クライアントから確認する。"""

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=str(SERVER_COMMAND),
            args=["stdio"],
            env=_test_environment(),
            cwd=REPOSITORY_ROOT,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await asyncio.wait_for(_assert_client_surface(session), timeout=20)

    asyncio.run(exercise())


def test_stdio_protocol_surface() -> None:
    """stdioはMCP JSON-RPC以外を混ぜずcanonical 50ツールを返す。"""

    _exercise_stdio()


def _free_port() -> int:
    """空いているlocalhostポートを1つ取得する。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_http_server(port: int, *, database_url: str | None = None) -> subprocess.Popen[str]:
    """テスト用Streamable HTTPプロセスを起動する。"""

    return subprocess.Popen(
        [
            # A Windows console launcher spawns Python as a child; launch the module
            # directly so health.pid and teardown refer to the actual server.
            sys.executable,
            "-m",
            "jvdata_bridge_mcp.cli",
            "serve",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPOSITORY_ROOT,
        env=_test_environment(database_url),
        start_new_session=os.name == "posix",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_health(port: int, process: subprocess.Popen[str]) -> dict[str, Any]:
    """HTTPサーバーの起動を待ち、health payloadを返す。"""

    for _ in range(100):
        try:
            response = httpx.get(
                f"http://127.0.0.1:{port}/health",
                timeout=0.2,
                trust_env=False,
            )
        except httpx.HTTPError:
            response = None
        if response is not None and response.status_code == 200:
            payload = response.json()
            assert isinstance(payload, dict)
            return payload
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(f"HTTP server exited with {process.returncode}: {stderr}")
        time.sleep(0.1)
    raise AssertionError("HTTP server did not become ready")


def _terminate_http_process(process: subprocess.Popen[str], server_pid: int | None = None) -> None:
    """POSIXのプロセスグループとWindowsの単一プロセスを共通手順で終了する。"""

    # Windows venv Python may itself be a redirector. Its actual server child owns
    # the socket and inherited pipes, and must also be stopped during teardown.
    if os.name == "nt" and server_pid is not None and server_pid != process.pid:
        try:
            os.kill(server_pid, signal.SIGTERM)
        except OSError:
            pass
    if process.poll() is None:
        if os.name == "posix" and hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except OSError:
                try:
                    process.terminate()
                except OSError:
                    pass
        else:
            try:
                process.terminate()
            except OSError:
                pass
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix" and hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except OSError:
                try:
                    process.kill()
                except OSError:
                    pass
        else:
            try:
                process.kill()
            except OSError:
                pass
        process.communicate(timeout=5)


def _exercise_http() -> None:
    """canonical Streamable HTTP公開面を実クライアントから確認する。"""

    port = _free_port()
    process = _start_http_server(port)
    server_pid = None
    try:
        health = _wait_for_health(port, process)
        assert health["status"] == "ok"
        assert health["server"] == "jvdata-bridge-mcp"
        assert health["tool_count"] == 50
        assert health["resource_count"] == 6
        assert isinstance(health["pid"], int) and health["pid"] > 0
        server_pid = health["pid"]
        if os.name != "nt":
            assert server_pid == process.pid
        assert health["host"] == "127.0.0.1"
        assert health["port"] == port
        assert isinstance(health["started_at"], str) and health["started_at"]
        assert isinstance(health["instance_id"], str) and len(health["instance_id"]) >= 16
        assert health["transport"] == "streamable-http"

        async def exercise() -> None:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), trust_env=False) as http_client:
                async with streamable_http_client(
                    f"http://127.0.0.1:{port}/mcp",
                    http_client=http_client,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await asyncio.wait_for(_assert_client_surface(session), timeout=25)

        asyncio.run(exercise())
    finally:
        _terminate_http_process(process, server_pid)


def test_streamable_http_protocol_surface() -> None:
    """Streamable HTTPはcanonical 50ツール一覧を返す。"""

    _exercise_http()


@pytest.fixture
def postgres_url() -> str:
    """専用のPostgreSQL検証環境が指定された場合だけ実接続を行う。"""

    value = os.environ.get("JVDATA_MCP_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set JVDATA_MCP_TEST_DATABASE_URL to an isolated test database.")
    return value


async def _assert_postgres_query(session: ClientSession) -> None:
    """実DBへのSELECTと、MCP境界での書き込みSQL拒否を確認する。"""

    await session.initialize()
    result = await session.call_tool(
        "sql_execute_custom_query",
        {"sql": "SELECT race_id FROM jv_data.races ORDER BY race_id"},
    )
    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["rows"] == [{"race_id": "integration-race"}]
    rejected = await session.call_tool("sql_execute_custom_query", {"sql": "DELETE FROM jv_data.races"})
    assert rejected.isError is True


def test_postgres_doctor_connects_read_only(postgres_url: str) -> None:
    """doctorの通常起動で実DBへ接続し、read-only transactionを使う。"""

    result = subprocess.run(
        [sys.executable, "-m", "jvdata_bridge_mcp.cli", "doctor"],
        cwd=REPOSITORY_ROOT,
        env=_test_environment(postgres_url),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["database"]["transaction_read_only"] is True


def test_postgres_stdio_query(postgres_url: str) -> None:
    """stdioサーバーの起動経路から実DBを参照する。"""

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "jvdata_bridge_mcp.cli", "stdio"],
            env=_test_environment(postgres_url),
            cwd=REPOSITORY_ROOT,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await asyncio.wait_for(_assert_postgres_query(session), timeout=20)

    asyncio.run(exercise())


def test_postgres_streamable_http_query(postgres_url: str) -> None:
    """HTTPサーバーの起動経路から実DBを参照する。"""

    port = _free_port()
    process = _start_http_server(port, database_url=postgres_url)
    server_pid = None
    try:
        server_pid = _wait_for_health(port, process)["pid"]

        async def exercise() -> None:
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=client) as (
                    read_stream,
                    write_stream,
                    _,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        await asyncio.wait_for(_assert_postgres_query(session), timeout=20)

        asyncio.run(exercise())
    finally:
        _terminate_http_process(process, server_pid)
