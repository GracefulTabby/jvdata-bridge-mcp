"""jvdata-bridge-mcpのコマンドラインインターフェース。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import signal
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.request import ProxyHandler, Request, build_opener

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from jvdata_bridge_mcp.config import (
    DATA_SCHEMA,
    MART_SCHEMA,
    REF_SCHEMA,
    JvDataConfigurationError,
    JvDataSettings,
)
from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.knowledge_resources import load_knowledge_resources
from jvdata_bridge_mcp.server import DEFAULT_HOST, DEFAULT_PORT, SERVER_NAME, run_server
from jvdata_bridge_mcp.sql_gateway import JvDataSqlGateway
from jvdata_bridge_mcp.tools.all import build_all_tools


@dataclass(frozen=True)
class ServerMetadata:
    """PIDファイルとhealth endpointで共有するサーバー識別情報。"""

    server: str
    pid: int
    host: str
    port: int
    started_at: str
    instance_id: str
    transport: str = "streamable-http"

    def as_payload(self) -> dict[str, object]:
        """JSONへ保存する管理情報を返す。"""

        return {
            "server": self.server,
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "started_at": self.started_at,
            "instance_id": self.instance_id,
            "transport": self.transport,
        }

    @classmethod
    def from_payload(cls, payload: object, pid_file: Path) -> ServerMetadata:
        """JSON payloadを検証済みのサーバー識別情報へ変換する。"""

        if not isinstance(payload, Mapping):
            raise ValueError(f"invalid pid metadata: {pid_file}")

        server = payload.get("server")
        if server != SERVER_NAME:
            raise ValueError(f"pid file is not for {SERVER_NAME}: {pid_file}")

        pid = payload.get("pid")
        port = payload.get("port")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError(f"invalid pid in file: {pid_file}")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
            raise ValueError(f"invalid port in file: {pid_file}")

        string_values: dict[str, str] = {}
        for name in ("host", "started_at", "instance_id"):
            value = payload.get(name)
            if not isinstance(value, str) or not value.strip() or any(char in value for char in "\r\n"):
                raise ValueError(f"invalid {name} in file: {pid_file}")
            string_values[name] = value

        transport = payload.get("transport", "streamable-http")
        if transport != "streamable-http":
            raise ValueError(f"unsupported transport in file: {pid_file}")

        return cls(
            server=SERVER_NAME,
            pid=pid,
            host=string_values["host"],
            port=port,
            started_at=string_values["started_at"],
            instance_id=string_values["instance_id"],
            transport=transport,
        )


def _json_print(payload: object) -> None:
    """安定した、人間が読みやすいJSON結果を表示する。"""

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _parser() -> argparse.ArgumentParser:
    """公開CLIのパーサーを構築する。"""

    parser = argparse.ArgumentParser(prog="jvdata-bridge-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("stdio", help="標準入出力でMCPサーバーを起動する。")

    serve = subparsers.add_parser("serve", help="Streamable HTTPでMCPサーバーを起動する。")
    serve.add_argument(
        "--transport",
        choices=("streamable-http",),
        default="streamable-http",
        help="HTTPトランスポートの実装。",
    )
    serve.add_argument("--host", default=DEFAULT_HOST)
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--pid-file", type=Path)

    subparsers.add_parser("doctor", help="設定とデータベース接続を検証する。")

    models = subparsers.add_parser("models", help="同梱モデルの状態確認と、任意のローカル学習モデルの導入。")
    model_commands = models.add_subparsers(dest="model_command", required=True)
    model_commands.add_parser("status", help="同梱モデルまたは明示導入したローカルモデルの状態。")
    install = model_commands.add_parser("install", help="任意に学習した検証済みローカルモデルを導入する。")
    install.add_argument(
        "--bundle", type=Path, required=True, help="prediction-model.jsonとmanifestを持つローカルフォルダー。"
    )
    package = model_commands.add_parser("package", help="採用基準を通過したモデルから配布用JSONとmanifestを作る。")
    package.add_argument("--model", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)

    status = subparsers.add_parser("status", help="ローカル管理中のサーバープロセスを確認する。")
    status.add_argument("--pid-file", required=True, type=Path)

    stop = subparsers.add_parser("stop", help="ローカル管理中のサーバープロセスを停止する。")
    stop.add_argument("--pid-file", required=True, type=Path)
    stop.add_argument("--timeout", type=float, default=10.0)

    return parser


def _new_server_metadata(
    *,
    host: str,
    port: int,
    instance_id: str | None = None,
    started_at: str | None = None,
) -> ServerMetadata:
    """現在のStreamable HTTPプロセス用の識別情報を作る。"""

    return ServerMetadata(
        server=SERVER_NAME,
        pid=os.getpid(),
        host=host,
        port=port,
        started_at=started_at or datetime.now(timezone.utc).isoformat(),
        instance_id=instance_id or secrets.token_hex(16),
    )


def _read_pid_metadata(pid_file: Path) -> ServerMetadata:
    """JSON形式のPIDメタデータを読み取り、必須識別情報を検証する。"""

    try:
        payload = json.loads(pid_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid or missing pid file: {pid_file}") from exc
    return ServerMetadata.from_payload(payload, pid_file)


def _windows_process_alive(pid: int) -> bool:
    """Windowsのプロセスハンドルから終了状態を確認する。"""

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            # 権限不足の場合は、生存している可能性を保守的に残す。health照合が
            # 最終的な管理対象判定を行うため、ここで他プロセスを終了させることはない。
            return ctypes.get_last_error() == 5
        exit_code = wintypes.DWORD()
        try:
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        # 想定外のWindows API差異がある環境では、標準APIへフォールバックする。
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True


def _process_alive(pid: int) -> bool:
    """現在のユーザーからプロセスが生存中と確認できるか返す。"""

    if os.name == "nt":
        return _windows_process_alive(pid)
    # 親プロセスが別のCLIプロセスである場合、終了した子がreapされるまで
    # POSIXのkill(pid, 0)は成功し続ける。Linuxの/procではその状態を区別できる。
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        stat_text = proc_stat.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        stat_text = ""
    if stat_text:
        closing_parenthesis = stat_text.rfind(")")
        if closing_parenthesis >= 0 and stat_text[closing_parenthesis + 2 :].startswith("Z "):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _write_pid_file(
    pid_file: Path | None,
    metadata: ServerMetadata | None = None,
) -> ServerMetadata | None:
    """管理情報をJSONとして原子的にPIDファイルへ保存する。"""

    if pid_file is None:
        return metadata
    resolved_metadata = metadata or _new_server_metadata(host=DEFAULT_HOST, port=DEFAULT_PORT)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = pid_file.with_name(f".{pid_file.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary_file.write_text(
            json.dumps(resolved_metadata.as_payload(), ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_file, pid_file)
    finally:
        temporary_file.unlink(missing_ok=True)
    return resolved_metadata


def _remove_pid_file(
    pid_file: Path | None,
    metadata: ServerMetadata | None = None,
) -> None:
    """同じinstance_idのPIDファイルだけを削除する。"""

    if pid_file is None or not pid_file.exists():
        return
    try:
        current = _read_pid_metadata(pid_file)
    except ValueError:
        # 壊れたファイルを、別起動の管理ファイルかもしれない状態で削除しない。
        return
    expected = metadata or _new_server_metadata(host=current.host, port=current.port, instance_id=current.instance_id)
    if current.pid == expected.pid and current.instance_id == expected.instance_id:
        pid_file.unlink(missing_ok=True)


def _health_url(metadata: ServerMetadata) -> str:
    """ワイルドカードbindをloopbackへ解決したhealth URLを作る。"""

    host = metadata.host.strip()
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    elif ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{metadata.port}/health"


def _read_health(metadata: ServerMetadata, *, timeout: float = 1.0) -> dict[str, object] | None:
    """管理情報の接続先からhealth payloadを取得する。"""

    try:
        request = Request(_health_url(metadata), method="GET")
        # プロセス管理のhealth照合は環境変数のHTTP proxyを経由させない。
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _managed_process_matches(metadata: ServerMetadata) -> bool:
    """PIDとhealth endpointが同一のMCP起動インスタンスを示すか確認する。"""

    if not _process_alive(metadata.pid):
        return False
    payload = _read_health(metadata)
    if payload is None or payload.get("status") != "ok":
        return False
    return all(
        payload.get(field) == getattr(metadata, field)
        for field in ("server", "pid", "host", "port", "started_at", "instance_id", "transport")
    )


def _signal_process(pid: int, *, force: bool = False) -> None:
    """POSIX/Windows共通の単一プロセス終了要求を送る。"""

    if os.name == "nt":
        # WindowsのSIGTERMはTerminateProcess相当で、プロセスツリーを誤って
        # 巻き込まない単一プロセス終了になる。
        os.kill(pid, signal.SIGTERM)
        return
    termination_signal = getattr(signal, "SIGKILL", signal.SIGTERM) if force else signal.SIGTERM
    os.kill(pid, termination_signal)


def _serve(args: argparse.Namespace) -> int:
    """Streamable HTTPサーバーを起動する。"""

    metadata = _new_server_metadata(host=args.host, port=args.port)
    _write_pid_file(args.pid_file, metadata)
    try:
        run_server(
            args.transport,
            host=args.host,
            port=args.port,
            instance_id=metadata.instance_id,
            started_at=metadata.started_at,
        )
    finally:
        _remove_pid_file(args.pid_file, metadata)
    return 0


def _stdio() -> int:
    """stdioサーバーを起動する。"""

    run_server("stdio")
    return 0


async def _doctor_async() -> int:
    """設定、データベース接続、主要read modelを検証する。"""

    configuration: dict[str, object] = {"valid": False}
    database_result: dict[str, object] = {"connected": False}
    read_models: dict[str, bool] = {}
    result: dict[str, object] = {
        "server": SERVER_NAME,
        "tool_count": 0,
        "resource_count": 0,
        "configuration": configuration,
        "database": database_result,
        "read_models": read_models,
    }
    database: JvDataDatabase | None = None
    try:
        settings = JvDataSettings.from_env()
        settings.sqlalchemy_url()
        configuration = {
            "valid": True,
            "database": settings.database,
            "host_configured": bool(settings.host or settings.database_url),
            "user_configured": bool(settings.user or settings.database_url),
            "schemas": {
                "data": DATA_SCHEMA,
                "mart": MART_SCHEMA,
                "ref": REF_SCHEMA,
            },
        }
        result["configuration"] = configuration
        database = JvDataDatabase(settings)
        resources = load_knowledge_resources()
        result["tool_count"] = len(
            build_all_tools(database, JvDataSqlGateway(database), knowledge_resources=resources)
        )
        result["resource_count"] = len(resources)
        relations = {
            "races": (DATA_SCHEMA, "races"),
            "race_cards": (MART_SCHEMA, "race_cards"),
            "latest_win_place_odds": (MART_SCHEMA, "latest_win_place_odds"),
        }
        async with database.read_connection(statement_timeout_seconds=settings.command_timeout_seconds) as connection:
            identity_result = await connection.execute(
                text(
                    "SELECT current_database() AS database_name, "
                    "current_user AS user_name, "
                    "current_setting('transaction_read_only') AS transaction_read_only"
                )
            )
            identity = identity_result.mappings().one()
            database_result = {
                "connected": True,
                "database": identity["database_name"],
                "user": identity["user_name"],
                "transaction_read_only": identity["transaction_read_only"] == "on",
            }
            result["database"] = database_result
            for name, (schema, relation) in relations.items():
                read_models[name] = await database.relation_exists(schema, relation, connection)
    except (JvDataConfigurationError, SQLAlchemyError, OSError, ValueError) as exc:
        result["error"] = type(exc).__name__
        _json_print(result)
        return 1
    finally:
        if database is not None:
            await database.dispose()

    ok: bool = bool(database_result.get("connected")) and all(read_models.values())
    result["ok"] = ok
    _json_print(result)
    return 0 if ok else 1


def _doctor() -> int:
    """同期CLIエントリーポイントから非同期doctor処理を実行する。"""

    return asyncio.run(_doctor_async())


def _status(args: argparse.Namespace) -> int:
    """コマンドライン上の秘密情報を公開せず、管理中のプロセスを確認する。"""

    try:
        metadata = _read_pid_metadata(args.pid_file)
    except ValueError as exc:
        _json_print({"ok": False, "error": str(exc)})
        return 1
    alive = _process_alive(metadata.pid)
    managed = alive and _managed_process_matches(metadata)
    if managed:
        status = "running"
        error: str | None = None
    elif alive:
        status = "unknown"
        error = "pid is alive but health identity does not match pid file"
    else:
        status = "stopped"
        error = None
    payload: dict[str, object] = {
        "ok": managed,
        "server": metadata.server,
        "pid": metadata.pid,
        "host": metadata.host,
        "port": metadata.port,
        "started_at": metadata.started_at,
        "instance_id": metadata.instance_id,
        "pid_file": str(args.pid_file),
        "status": status,
    }
    if error is not None:
        payload["error"] = error
    _json_print(payload)
    return 0 if managed else 1


def _stop(args: argparse.Namespace) -> int:
    """管理中のプロセスを終了し、そのPIDファイルを整理する。"""

    try:
        metadata = _read_pid_metadata(args.pid_file)
    except ValueError as exc:
        _json_print({"ok": False, "error": str(exc)})
        return 1

    alive = _process_alive(metadata.pid)
    if not alive:
        _remove_pid_file(args.pid_file, metadata)
        _json_print(
            {
                "ok": True,
                "server": metadata.server,
                "pid": metadata.pid,
                "host": metadata.host,
                "port": metadata.port,
                "started_at": metadata.started_at,
                "instance_id": metadata.instance_id,
                "pid_file": str(args.pid_file),
                "status": "stopped",
            }
        )
        return 0

    if not _managed_process_matches(metadata):
        _json_print(
            {
                "ok": False,
                "server": metadata.server,
                "pid": metadata.pid,
                "host": metadata.host,
                "port": metadata.port,
                "started_at": metadata.started_at,
                "instance_id": metadata.instance_id,
                "pid_file": str(args.pid_file),
                "status": "unknown",
                "error": "pid is alive but health identity does not match pid file; refusing to stop it",
            }
        )
        return 1

    try:
        _signal_process(metadata.pid)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + max(0.1, args.timeout)
    while _process_alive(metadata.pid) and time.monotonic() < deadline:
        time.sleep(0.1)

    alive = _process_alive(metadata.pid)
    if alive and _managed_process_matches(metadata):
        # SIGTERMを処理しない自プロセスだけを、再度health照合してから強制終了する。
        try:
            _signal_process(metadata.pid, force=True)
        except (ProcessLookupError, PermissionError):
            pass
        force_deadline = time.monotonic() + min(2.0, max(0.1, args.timeout))
        while _process_alive(metadata.pid) and time.monotonic() < force_deadline:
            time.sleep(0.05)
        alive = _process_alive(metadata.pid)

    if not alive:
        _remove_pid_file(args.pid_file, metadata)
    payload = {
        "ok": not alive,
        "server": metadata.server,
        "pid": metadata.pid,
        "host": metadata.host,
        "port": metadata.port,
        "started_at": metadata.started_at,
        "instance_id": metadata.instance_id,
        "pid_file": str(args.pid_file),
        "status": "stopped" if not alive else "running",
    }
    _json_print(payload)
    return 0 if not alive else 1


def main(argv: Sequence[str] | None = None) -> int:
    """公開コマンドラインインターフェースを実行する。"""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    args = _parser().parse_args(argv)
    if args.command == "stdio":
        return _stdio()
    if args.command == "serve":
        return _serve(args)
    if args.command == "doctor":
        return _doctor()
    if args.command == "models":
        from jvdata_bridge_mcp.ml.artifacts import install_bytes, model_status, prepare_bundle

        try:
            if args.model_command == "status":
                result = model_status()
            elif args.model_command == "package":
                result = prepare_bundle(args.model, args.output)
            else:
                result = install_bytes(
                    (args.bundle / "prediction-model.manifest.json").read_bytes(),
                    (args.bundle / "prediction-model.json").read_bytes(),
                )
            _json_print(result)
            return 0
        except (ValueError, OSError, KeyError, TypeError) as error:
            _json_print({"error": str(error), "installed": False})
            return 1
    if args.command == "status":
        return _status(args)
    if args.command == "stop":
        return _stop(args)
    raise AssertionError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
