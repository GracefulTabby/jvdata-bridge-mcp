"""指定馬の直前21日調教詳細を取得するMCPツール。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import training_before_target_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.training._normalization import (
    display_horse_number,
    display_training_center,
    is_plausible_four_furlongs_seconds,
    relative_training_scores,
    valid_training_quality_sql,
)

HorseNumber = Annotated[str, Field(description="出走表上の馬番")]
RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]
_HORSE_NUMBER_PATTERN = re.compile(r"(?:0?[1-9]|1[0-8])\Z")
_VALID_QUALITY = valid_training_quality_sql("s")


def _normalize_horse_number(value: str) -> int:
    """馬番をDB照会用の整数へ正規化する。"""

    if not isinstance(value, str) or _HORSE_NUMBER_PATTERN.fullmatch(value) is None:
        raise ValueError("horse_number must be an ASCII number from 1 to 18")
    return int(value)


class TrainingHorseDetail(BaseModel):
    """指定馬のcondition/trendと直近調教履歴。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    horse_number: str = Field(default="", description="出走表上の馬番。一括取得時は空")
    horse_name: str = Field(default="", description="馬名。一括取得時は空")
    condition_score: float | None = Field(default=None, description="自己内相対condition score。比較不能時はnull")
    trend_score: float | None = Field(default=None, description="自己内相対trend score。比較不足時は5")
    best_times: dict[str, dict[str, object]] = Field(default_factory=dict, description="直前期間における最良時計")
    recent_history: list[dict[str, object]] = Field(default_factory=list, description="指定数の調教履歴")
    score_semantics: str = Field(
        default="descriptive_clock_comparison; fitness_unknown", description="condition/trend scoreの意味"
    )
    session_count: int = Field(default=0, description="有効調教の件数。一括取得時は全頭の合計")
    comparable_session_count: int = Field(
        default=0, description="自己内比較に使える調教の件数。一括取得時は全頭の合計"
    )
    all_history_returned: bool = Field(
        default=False, description="対象馬の履歴を省略なく返したか。一括取得時は全頭を判定"
    )
    best_sessions_by_cohort: list[dict[str, object]] = Field(default_factory=list)
    missing_reason: str | None = None
    coverage: str = "stored_HC_WC_miho_ritto; local_training_unknown"
    observed_effort: str | None = None
    fitness_inference: str | None = None


class TrainingEntryDetailOutput(TrainingHorseDetail):
    items: list[TrainingHorseDetail] | None = None
    count: int | None = None
    scope: str = "single_horse; previous_21_days; HC_WC_only"


class TrainingRaceDetailOutput(TrainingEntryDetailOutput):
    race_code: str
    items: list[TrainingHorseDetail]
    count: int
    scope: str = "all_active_entries; previous_21_days; HC_WC_only"


_SQL = f"""
WITH target AS MATERIALIZED (
    SELECT DISTINCT ON (c.race_id, c.horse_number)
        c.horse_number,
        c.horse_id,
        c.horse_name,
        r.race_date
    FROM jv_mart.race_cards AS c
    JOIN jv_data.races AS r ON r.race_id = c.race_id
    WHERE c.race_id = :race_code
      AND c.horse_number = :horse_number
      AND c.horse_id IS NOT NULL

    ORDER BY c.race_id, c.horse_number, c.horse_id
)
SELECT
    t.horse_number,
    t.horse_id,
    t.horse_name,
    s.training_date,
    s.training_time,
    s.training_center_code,
    s.course_code,
    s.direction_code,
    s.training_type,
    s.six_furlongs_seconds,
    s.source_record_id,
    s.five_furlongs_seconds,
    s.four_furlongs_seconds,
    s.three_furlongs_seconds,
    s.two_furlongs_seconds,
    s.lap_400_200_seconds,
    s.lap_200_0_seconds,
    s.quality_status,
    s.quality_issue_code
FROM target AS t
LEFT JOIN LATERAL (
    SELECT
        s.training_date,
        s.training_time,
        s.training_center_code,
        s.course_code,
        s.direction_code,
        s.training_type,
        s.six_furlongs_seconds,
        s.source_record_id,
        s.five_furlongs_seconds,
        s.four_furlongs_seconds,
        s.three_furlongs_seconds,
        s.two_furlongs_seconds,
        s.lap_400_200_seconds,
        s.lap_200_0_seconds,
        s.quality_status,
        s.quality_issue_code
    FROM jv_mart.training_sessions AS s
    WHERE s.horse_id = t.horse_id
      AND s.training_date >= t.race_date - INTERVAL '21 days'
      AND {training_before_target_sql("s", "t.race_date")}
      AND {_VALID_QUALITY}
    ORDER BY s.training_date DESC, s.training_time DESC NULLS LAST, s.source_sequence DESC NULLS LAST
) AS s ON TRUE
ORDER BY s.training_date DESC NULLS LAST, s.training_time DESC NULLS LAST
"""


def _float(row: Mapping[str, object], key: str) -> float | None:
    """SQL値をfloatへ変換する。"""

    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _history_item(row: Mapping[str, object]) -> dict[str, object]:
    """SQL行を調教履歴の出力項目へ変換する。"""

    training_date = row.get("training_date")
    training_time = row.get("training_time")
    training_at = (
        f"{training_date}T{training_time}"
        if training_date is not None and training_time is not None
        else str(training_date)
        if training_date is not None
        else None
    )
    f4 = _float(row, "four_furlongs_seconds")
    f3 = _float(row, "three_furlongs_seconds")
    f2 = _float(row, "two_furlongs_seconds")
    f1 = _float(row, "lap_200_0_seconds")
    laps = [round(f4 - f3, 2), round(f3 - f2, 2), round(f2 - f1, 2), f1] if None not in {f4, f3, f2, f1} else []
    return {
        "datetime": training_at,
        "training_center": display_training_center(row.get("training_center_code")),
        "training_type": row.get("training_type") or "unknown",
        "course": row.get("course_code"),
        "direction": row.get("direction_code"),
        "time_4f": f4,
        "time_6f": _float(row, "six_furlongs_seconds"),
        "source_record_id": row.get("source_record_id"),
        "time_3f": f3,
        "time_2f": f2,
        "last_1f": f1,
        "lap_times": laps,
        "lap_order": "start_to_finish",
        "evaluation_comment": None,
        "evaluation_points": None,
    }


def _best_payload(rows: list[Mapping[str, object]], key: str) -> dict[str, object] | None:
    """期間内最良時計を公開形式へ変換する。"""

    candidates = [row for row in rows if _float(row, key) is not None]
    if key == "four_furlongs_seconds":
        candidates = [row for row in candidates if is_plausible_four_furlongs_seconds(_float(row, key))]
    if not candidates:
        return None
    selected = min(candidates, key=lambda row: float(_float(row, key) or float("inf")))
    training_date = selected.get("training_date")
    training_time = selected.get("training_time")
    recorded_at = (
        f"{training_date}T{training_time}"
        if training_date is not None and training_time is not None
        else str(training_date)
        if training_date is not None
        else None
    )
    return {
        "time": _float(selected, key),
        "training_type": selected.get("training_type") or "unknown",
        "recorded_at": recorded_at,
        "session": _history_item(selected),
    }


def build_training_entry_detail_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したtraining_entry_detail関数を作る。"""

    async def training_entry_detail(
        horse_number: HorseNumber = "",
        race_code: RaceCode = "",
        all_horses: Annotated[bool, Field(description="全出走馬を一括取得。この場合馬番は省略可能")] = False,
        history_limit: Annotated[int, Field(description="返す履歴数。0なら21日内を全件返す")] = 3,
    ) -> TrainingEntryDetailOutput:
        """直前21日調教を取得する。all_horses=trueで全頭一括、history_limit=0で全履歴。

        最良時計には同じ一本の4F/1F/6Fと場所を添える。時計の自己内評価は状態の確率ではない。
        追い方・併せ・北海道での調整が未収録ならunknownとする。
        """

        normalized_horse_number = None if all_horses else _normalize_horse_number(horse_number)
        if history_limit < 0:
            raise ValueError("history_limit must be nonnegative")
        rows = await fetch_rows(
            database,
            _SQL.replace("AND c.horse_number = :horse_number", "") if all_horses else _SQL,
            {"horse_number": normalized_horse_number, "race_code": race_code, "limit": 100},
        )
        if all_horses:
            grouped: dict[int, list[dict[str, object]]] = {}
            for row in rows:
                grouped.setdefault(int(row["horse_number"]), []).append(row)
            items = [_detail(race_code, grouped[n], n, history_limit) for n in sorted(grouped)]
            return TrainingRaceDetailOutput(
                race_code=race_code,
                items=items,
                count=len(items),
                session_count=sum(item.session_count for item in items),
                comparable_session_count=sum(item.comparable_session_count for item in items),
                all_history_returned=all(item.all_history_returned for item in items),
            )
        detail = _detail(race_code, rows, normalized_horse_number, history_limit)
        return TrainingEntryDetailOutput(**detail.model_dump())

    return training_entry_detail


def _detail(
    race_code: str, rows: list[dict[str, object]], horse_number: int | None, history_limit: int
) -> TrainingHorseDetail:
    """取得済みセッションから単頭と一括で同じ詳細を作る。"""

    first = rows[0] if rows else {}
    sessions = [row for row in rows if row.get("training_date") is not None]
    condition, trend, comparable_count = relative_training_scores(sessions)
    cohorts: dict[tuple[object, object, object], list[dict[str, object]]] = {}
    for row in sessions:
        key = (row.get("training_type"), row.get("training_center_code"), row.get("course_code"))
        cohorts.setdefault(key, []).append(row)
    strongest = [_best_payload(group, "four_furlongs_seconds") for group in cohorts.values()]
    best_times: dict[str, dict[str, object]] = {}
    best_f4 = _best_payload(sessions, "four_furlongs_seconds")
    best_f1 = _best_payload(sessions, "lap_200_0_seconds")
    if best_f4 is not None:
        best_times["f4"] = best_f4
    if best_f1 is not None:
        best_times["last_1f"] = best_f1
    return TrainingHorseDetail(
        race_code=race_code,
        horse_number=display_horse_number(first.get("horse_number") or horse_number),
        horse_name=str(first.get("horse_name") or ""),
        condition_score=condition,
        trend_score=trend,
        best_times=best_times,
        recent_history=[_history_item(row) for row in (sessions[:history_limit] if history_limit else sessions)],
        session_count=len(sessions),
        comparable_session_count=comparable_count,
        all_history_returned=history_limit == 0 or len(sessions) <= history_limit,
        best_sessions_by_cohort=[item for item in strongest if item is not None],
        missing_reason=None if sessions else "no_valid_stored_HC_WC_sessions_in_previous_21_days",
        score_semantics=(
            "condition: 直前21日の最新調教種別における有効4F時計の自己内相対score。"
            " 比較は同種・同センター・同コース。trend: 直近2本とそれ以前の差分。"
            " 時計不足時はnullまたは中立5。遅い調整時計も含むためDを不調と解釈しない。"
        ),
    )
