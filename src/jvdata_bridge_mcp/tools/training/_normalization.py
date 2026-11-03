"""調教分析で共有する品質・時計境界と自己内スコア計算。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import mean, pstdev
from typing import Final

FOUR_FURLONGS_MIN_SECONDS: Final[float] = 35.0
FOUR_FURLONGS_MAX_SECONDS: Final[float] = 100.0
VALID_QUALITY_STATUSES: Final[frozenset[str]] = frozenset({"ok", "valid"})
TRAINING_CENTER_NAMES: Final[dict[str, str]] = {
    "0": "美浦",
    "1": "栗東",
    "miho": "美浦",
    "ritto": "栗東",
    "美浦": "美浦",
    "栗東": "栗東",
}


def valid_four_furlongs_sql(column: str) -> str:
    """固定SQLで使う4ハロン時計の妥当性条件を返す。"""

    return f"{column} BETWEEN {FOUR_FURLONGS_MIN_SECONDS} AND {FOUR_FURLONGS_MAX_SECONDS}"


def valid_training_quality_sql(alias: str = "t") -> str:
    """固定SQLで使う正規化調教品質の条件を返す。"""

    return f"{alias}.quality_status IN ('ok', 'valid')"


def is_valid_training_quality(value: object) -> bool:
    """調教行が分析対象として許容された品質状態かを判定する。"""

    return str(value or "").strip().lower() in VALID_QUALITY_STATUSES


def display_horse_number(value: object) -> str:
    """公開調教payloadの馬番を2桁文字列へ整える。"""

    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return str(value or "")
    return f"{number:02d}" if 1 <= number <= 18 else str(number)


def display_training_center(value: object) -> str | None:
    """調教拠点コードを公開名へ正規化する。未知値はコードを保持する。"""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return TRAINING_CENTER_NAMES.get(text.lower(), text)


def horse_number_sort_key(value: object) -> tuple[int, str]:
    """馬番を数値順に並べ、異常値は末尾へ送る。"""

    text = str(value or "")
    try:
        return int(text), text
    except ValueError:
        return 99, text


def _row_float(row: Mapping[str, object], key: str) -> float | None:
    """SQL行の数値を安全にfloatへ変換する。"""

    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def relative_training_scores(rows: Sequence[Mapping[str, object]]) -> tuple[float | None, float | None, int]:
    """直前期間の同種4F時計からcondition/trendを自己内相対で返す。

    ``rows`` は新しい順に渡す。品質不良、または4F時計の物理境界外の行は
    比較から除外する。比較できる行がない場合に一律の低評価を返さず、
    ``(None, None, 0)`` としてサンプル不足を呼び出し側に伝える。
    """

    valid_rows: list[Mapping[str, object]] = [
        row
        for row in rows
        if is_valid_training_quality(row.get("quality_status"))
        and is_plausible_four_furlongs_seconds(_row_float(row, "four_furlongs_seconds"))
    ]
    if not valid_rows:
        return None, None, 0

    latest_type = str(valid_rows[0].get("training_type") or "unknown")
    latest = valid_rows[0]
    comparable = [
        row
        for row in valid_rows
        if str(row.get("training_type") or "unknown") == latest_type
        and row.get("training_center_code") == latest.get("training_center_code")
        and row.get("course_code") == latest.get("course_code")
    ]
    values = [value for row in comparable for value in [_row_float(row, "four_furlongs_seconds")] if value is not None]
    if not values:
        return None, None, 0

    average = mean(values)
    deviation = pstdev(values)
    latest = values[0]
    condition = 5.0 if deviation == 0 else _clamp_score(5.0 + ((average - latest) / deviation) * 2.0)
    recent_values = values[:2]
    older_values = values[2:]
    if deviation == 0 or not older_values:
        trend = 5.0
    else:
        recent_average = mean(recent_values)
        older_average = mean(older_values)
        trend = _clamp_score(5.0 + ((older_average - recent_average) / deviation) * 2.0)
    return round(condition, 1), round(trend, 1), len(values)


def is_plausible_four_furlongs_seconds(value: float | None) -> bool:
    """分析に使える4ハロン時計かを判定する。"""

    return value is not None and FOUR_FURLONGS_MIN_SECONDS <= value <= FOUR_FURLONGS_MAX_SECONDS


def _clamp_score(value: float) -> float:
    """自己内スコアを0〜10へ丸める。"""

    return max(0.0, min(10.0, round(value, 1)))
