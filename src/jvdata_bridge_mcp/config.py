"""jvdata bridge MCPの接続設定。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url

ENV_PREFIX: str = "JVDATA_BRIDGE_MCP_"
DATA_SCHEMA: str = "jv_data"
MART_SCHEMA: str = "jv_mart"
REF_SCHEMA: str = "jv_ref"


class JvDataConfigurationError(ValueError):
    """jvdata bridge MCPの設定が不正な場合に送出する。"""


def _read_positive_int(environ: Mapping[str, str], name: str, default: int, *, allow_zero: bool = False) -> int:
    """環境変数から正の整数を読み取る。"""

    raw_value = environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise JvDataConfigurationError(f"{name} must be an integer") from exc
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise JvDataConfigurationError(f"{name} must be >= {minimum}")
    return value


def _first_value(environ: Mapping[str, str], *names: str) -> str | None:
    """最初に設定されている空でない環境変数を返す。"""

    for name in names:
        value = environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


@dataclass(frozen=True, slots=True)
class JvDataSettings:
    """MCP専用PostgreSQL接続とread modelの設定。

    日付範囲は設定項目に含めない。データベース側に存在する全期間を
    データの収録範囲として扱い、個別ツールの検索条件や予測時点だけが
    必要な境界を持つ。
    """

    database_url: str | None = None
    host: str | None = None
    port: int = 5432
    database: str = "jvdata"
    user: str | None = None
    password: str | None = None
    sslmode: str = "disable"
    pool_size: int = 5
    max_overflow: int = 5
    pool_timeout_seconds: int = 10
    connect_timeout_seconds: int = 5
    command_timeout_seconds: int = 60
    max_rows: int = 200

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "JvDataSettings":
        """環境変数から設定を作る。

        ``JVDATA_BRIDGE_MCP_*`` だけを読み取る。それ以外の環境変数は
        参照しない。
        """

        if environ is None:
            load_dotenv()
        source: Mapping[str, str] = environ if environ is not None else os.environ
        return cls(
            database_url=_first_value(source, f"{ENV_PREFIX}DATABASE_URL"),
            host=_first_value(source, f"{ENV_PREFIX}DB_HOST"),
            port=_read_positive_int(source, f"{ENV_PREFIX}DB_PORT", 5432),
            database=_first_value(source, f"{ENV_PREFIX}DB_NAME") or "jvdata",
            user=_first_value(source, f"{ENV_PREFIX}DB_USER"),
            password=_first_value(source, f"{ENV_PREFIX}DB_PASSWORD"),
            sslmode=_first_value(source, f"{ENV_PREFIX}SSLMODE") or "disable",
            pool_size=_read_positive_int(source, f"{ENV_PREFIX}POOL_SIZE", 5),
            max_overflow=_read_positive_int(source, f"{ENV_PREFIX}MAX_OVERFLOW", 5, allow_zero=True),
            pool_timeout_seconds=_read_positive_int(source, f"{ENV_PREFIX}POOL_TIMEOUT_SECONDS", 10),
            connect_timeout_seconds=_read_positive_int(source, f"{ENV_PREFIX}CONNECT_TIMEOUT_SECONDS", 5),
            command_timeout_seconds=_read_positive_int(source, f"{ENV_PREFIX}COMMAND_TIMEOUT_SECONDS", 60),
            max_rows=_read_positive_int(source, f"{ENV_PREFIX}MAX_ROWS", 200),
        )

    def sqlalchemy_url(self) -> URL:
        """SQLAlchemy用のURLを生成する。秘密値はURL文字列へ直書きしない。"""

        if self.database_url:
            url = make_url(self.database_url)
            if url.drivername == "postgresql":
                url = url.set(drivername="postgresql+psycopg")
            return url

        missing: list[str] = []
        if not self.host:
            missing.append(f"{ENV_PREFIX}DB_HOST")
        if not self.user:
            missing.append(f"{ENV_PREFIX}DB_USER")
        if self.password is None:
            missing.append(f"{ENV_PREFIX}DB_PASSWORD")
        if missing:
            names = ", ".join(missing)
            raise JvDataConfigurationError(f"jvdata connection settings are missing: {names}")

        return URL.create(
            drivername="postgresql+psycopg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )
