"""Streamable HTTPプロセス管理の識別情報と安全な停止を検証する。"""

from __future__ import annotations

import json
from argparse import Namespace
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from jvdata_bridge_mcp import cli
from jvdata_bridge_mcp.config import JvDataSettings


def _metadata(*, instance_id: str = "instance-a") -> cli.ServerMetadata:
    """テスト用の固定サーバーメタデータを作る。"""

    return cli.ServerMetadata(
        server=cli.SERVER_NAME,
        pid=12345,
        host="127.0.0.1",
        port=9876,
        started_at="2026-08-23T00:00:00+00:00",
        instance_id=instance_id,
    )


def test_pid_file_contains_identity_metadata(tmp_path: Path) -> None:
    """PIDだけでなくhealth照合に必要な起動情報をJSONへ保存する。"""

    pid_file = tmp_path / "server.pid"
    metadata = _metadata()

    cli._write_pid_file(pid_file, metadata)

    payload = json.loads(pid_file.read_text(encoding="utf-8"))
    assert payload == metadata.as_payload()
    assert cli._read_pid_metadata(pid_file) == metadata


def test_status_and_stop_refuse_a_pid_reuse_without_matching_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じPIDを別プロセスが再利用してもhealth不一致なら停止しない。"""

    pid_file = tmp_path / "server.pid"
    metadata = _metadata()
    cli._write_pid_file(pid_file, metadata)
    monkeypatch.setattr(cli, "_process_alive", lambda pid: True)
    monkeypatch.setattr(
        cli,
        "_read_health",
        lambda current: {**current.as_payload(), "instance_id": "another-instance", "status": "ok"},
    )
    stop_calls: list[int] = []
    monkeypatch.setattr(cli, "_signal_process", lambda pid, force=False: stop_calls.append(pid))
    args = Namespace(pid_file=pid_file, timeout=0.1)

    assert cli._status(args) == 1
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["status"] == "unknown"
    assert status_payload["ok"] is False

    assert cli._stop(args) == 1
    stop_payload = json.loads(capsys.readouterr().out)
    assert stop_payload["status"] == "unknown"
    assert stop_payload["ok"] is False
    assert stop_calls == []
    assert pid_file.exists()


def test_stop_terminates_only_a_matching_health_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """health identityが一致する場合だけ停止要求を送り、管理ファイルを整理する。"""

    pid_file = tmp_path / "server.pid"
    metadata = _metadata()
    cli._write_pid_file(pid_file, metadata)
    state = {"alive": True}
    stop_calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(cli, "_process_alive", lambda pid: state["alive"])
    monkeypatch.setattr(cli, "_read_health", lambda current: {**current.as_payload(), "status": "ok"})

    def stop(pid: int, *, force: bool = False) -> None:
        stop_calls.append((pid, force))
        state["alive"] = False

    monkeypatch.setattr(cli, "_signal_process", stop)

    assert cli._stop(Namespace(pid_file=pid_file, timeout=0.1)) == 0
    assert stop_calls == [(metadata.pid, False)]
    assert not pid_file.exists()


def test_doctor_reports_tool_and_resource_counts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """doctorが公開ツールとResourceの件数を返す。"""

    settings = JvDataSettings(database_url="postgresql://reader@invalid/jvdata")
    seen_timeouts: list[int] = []

    class FakeResult:
        def mappings(self) -> FakeResult:
            return self

        def one(self) -> dict[str, str]:
            return {
                "database_name": "jvdata",
                "user_name": "reader",
                "transaction_read_only": "on",
            }

    class FakeConnection:
        async def execute(self, statement: object) -> FakeResult:
            return FakeResult()

    class FakeDatabase:
        def __init__(self, resolved_settings: JvDataSettings) -> None:
            self.settings = resolved_settings

        @asynccontextmanager
        async def read_connection(self, *, statement_timeout_seconds: int) -> object:
            seen_timeouts.append(statement_timeout_seconds)
            yield FakeConnection()

        async def relation_exists(
            self,
            schema: str,
            relation: str,
            connection: FakeConnection,
        ) -> bool:
            return True

        async def dispose(self) -> None:
            return None

    monkeypatch.setattr(cli.JvDataSettings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli, "JvDataDatabase", FakeDatabase)

    assert cli._doctor() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["tool_count"] == 50
    assert payload["resource_count"] == 6
    assert seen_timeouts == [60]
