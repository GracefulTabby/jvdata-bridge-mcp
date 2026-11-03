"""脚質コードをread model横断で一貫して扱うための正規化。"""

from __future__ import annotations

from typing import Final

UNKNOWN_RUNNING_STYLE: Final[str] = "unknown"

_RUNNING_STYLE_ALIASES: Final[dict[str, str]] = {
    "1": "front_runner",
    "逃げ": "front_runner",
    "front": "front_runner",
    "front_runner": "front_runner",
    "front-runner": "front_runner",
    "2": "stalker",
    "先行": "stalker",
    "stalker": "stalker",
    "3": "closer",
    "差し": "closer",
    "closer": "closer",
    "4": "deep_closer",
    "追込": "deep_closer",
    "deep_closer": "deep_closer",
    "deep-closer": "deep_closer",
}


def normalize_running_style(value: str | None) -> str:
    """データソースの脚質値を安定した英字ラベルへ変換する。"""

    if value is None:
        return UNKNOWN_RUNNING_STYLE
    return _RUNNING_STYLE_ALIASES.get(value.strip().casefold(), UNKNOWN_RUNNING_STYLE)


def normalized_running_style_sql(column: str) -> str:
    """固定SQL内で使う、脚質を正規化するCASE式を返す。

    ``column`` は各ツールが定数として渡す列参照であり、利用者入力からは
    組み立てない。
    """

    return f"""
        CASE LOWER(COALESCE({column}, ''))
            WHEN '1' THEN 'front_runner'
            WHEN '逃げ' THEN 'front_runner'
            WHEN 'front' THEN 'front_runner'
            WHEN 'front_runner' THEN 'front_runner'
            WHEN 'front-runner' THEN 'front_runner'
            WHEN '2' THEN 'stalker'
            WHEN '先行' THEN 'stalker'
            WHEN 'stalker' THEN 'stalker'
            WHEN '3' THEN 'closer'
            WHEN '差し' THEN 'closer'
            WHEN 'closer' THEN 'closer'
            WHEN '4' THEN 'deep_closer'
            WHEN '追込' THEN 'deep_closer'
            WHEN 'deep_closer' THEN 'deep_closer'
            WHEN 'deep-closer' THEN 'deep_closer'
            ELSE 'unknown'
        END
    """
