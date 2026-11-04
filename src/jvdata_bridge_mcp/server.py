"""スタンドアロンjvdata bridgeサーバーのFastMCP構成ルート。"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from jvdata_bridge_mcp.async_runtime import run_async
from jvdata_bridge_mcp.config import JvDataSettings
from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.knowledge_resources import load_knowledge_resources
from jvdata_bridge_mcp.sql_gateway import JvDataSqlGateway
from jvdata_bridge_mcp.tools.all import build_all_tools

logger = logging.getLogger(__name__)

SERVER_NAME: str = "jvdata-bridge-mcp"
DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 8765
Transport = Literal["stdio", "streamable-http"]


def create_server(
    database: JvDataDatabase | None = None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    instance_id: str | None = None,
    started_at: str | None = None,
) -> FastMCP:
    """スタンドアロンFastMCPサーバーを作成する。

    サーバーは設計上、1つのデータソースだけを使用する。接続元アプリケーションの実装や
    無関係な外部モジュールを調べたり、複数のデータ経路から選択したりしない。
    """

    if not host.strip():
        raise ValueError("host must not be empty")
    if not 1 <= port <= 65_535:
        raise ValueError("port must be between 1 and 65535")
    resolved_instance_id = instance_id or secrets.token_hex(16)
    resolved_started_at = started_at or datetime.now(timezone.utc).isoformat()
    if not resolved_instance_id.strip():
        raise ValueError("instance_id must not be empty")
    if not resolved_started_at.strip():
        raise ValueError("started_at must not be empty")

    resolved_database: JvDataDatabase = database or JvDataDatabase(JvDataSettings.from_env())
    server = FastMCP(
        SERVER_NAME,
        host=host,
        port=port,
        instructions=(
            "予想・レビュー前にknowledge_listで関連するナレッジを探し、knowledge_read(uri)で本文を読んでください。"
            "本文のURI・content_sha256・supporting_evidence_periodsを記録し、根拠期間と適用範囲を確認してください。"
            "同じ内容はMCP Resourcesからも参照できます。"
        ),
    )
    public_resources = load_knowledge_resources()
    public_tools = build_all_tools(
        resolved_database,
        JvDataSqlGateway(resolved_database),
        knowledge_resources=public_resources,
    )

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health_check(_: Request) -> JSONResponse:
        """DBへ問い合わせずに、プロセスと公開ツール構成の状態を返す。"""

        return JSONResponse(
            {
                "status": "ok",
                "server": SERVER_NAME,
                "tool_count": len(public_tools),
                "resource_count": len(public_resources),
                "pid": os.getpid(),
                "host": host,
                "port": port,
                "started_at": resolved_started_at,
                "instance_id": resolved_instance_id,
                "transport": "streamable-http",
            }
        )

    for tool in public_tools:
        server.add_tool(
            tool,
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
    for resource in public_resources:
        server.add_resource(resource)
    return server


def run_server(
    transport: Transport,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    instance_id: str | None = None,
    started_at: str | None = None,
) -> None:
    """stdioまたはStreamable HTTPでサーバーを起動する。"""

    server: FastMCP = create_server(
        host=host,
        port=port,
        instance_id=instance_id,
        started_at=started_at,
    )
    logger.info("Starting %s with %s transport", SERVER_NAME, transport)
    if transport == "stdio":
        run_async(server.run_stdio_async)
    elif transport == "streamable-http":
        run_async(server.run_streamable_http_async)
    else:
        raise ValueError(f"unsupported transport: {transport}")


def main() -> None:
    """モジュールを直接実行した場合にstdioで起動する。"""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    run_server("stdio")


if __name__ == "__main__":
    main()
