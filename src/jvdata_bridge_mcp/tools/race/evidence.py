"""確定レースのラップと馬別事実を取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._result_policy import ResultScope, include_provisional
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.race._laps import lap_segment_lengths, lap_window_comparison, opening_segment_meters
from jvdata_bridge_mcp.tools.race._ledger import runner_status

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class RaceLaps(BaseModel):
    """レース全体のラップ事実。"""

    model_config = ConfigDict(extra="forbid")

    front_3f: float | None = Field(default=None, description="前半3ハロン。秒")
    front_4f: float | None = Field(default=None, description="前半4ハロン。秒")
    back_3f: float | None = Field(default=None, description="後半3ハロン。秒")
    back_4f: float | None = Field(default=None, description="後半4ハロン。秒")
    lap_times: list[float] = Field(default_factory=list, description="区間ごとのラップ。秒")
    lap_segment_lengths_meters: list[int] | None = None
    front_three_segment_meters: int | None = None
    front_four_segment_meters: int | None = None
    lap_windows: dict[str, dict[str, object]] = Field(default_factory=dict)
    source_field_semantics: str = "RA前3/前4ハロン; leading_recorded_segments_may_include_remainder"


class FinishPositionFact(BaseModel):
    """着順または取消・除外の事実。"""

    model_config = ConfigDict(extra="forbid")

    raw: str | None = Field(default=None, description="JRAデータの着順原文")
    value: int | None = Field(default=None, description="確定着順")
    status: str = Field(description="着順データの状態")
    excluded_from_aggregates: bool = Field(default=False, description="集計対象外か")
    excluded_reason: str | None = Field(default=None, description="集計対象外の理由")


class RunningTimeFact(BaseModel):
    """走破タイムの原文と秒値。"""

    model_config = ConfigDict(extra="forbid")

    raw: str | None = Field(default=None, description="走破タイム原文")
    seconds: float | None = Field(default=None, description="走破タイム。秒")
    formatted: str | None = Field(default=None, description="走破タイム。分:秒.1")


class TimeDifferenceFact(BaseModel):
    """勝ち馬とのタイム差。"""

    model_config = ConfigDict(extra="forbid")

    raw: str | None = Field(default=None, description="タイム差原文")
    seconds: float | None = Field(default=None, description="タイム差。秒")


class CornerPositionsFact(BaseModel):
    """各コーナーの通過順位。"""

    model_config = ConfigDict(extra="forbid")

    first_corner: int | None = Field(default=None, description="第1コーナー")
    second_corner: int | None = Field(default=None, description="第2コーナー")
    third_corner: int | None = Field(default=None, description="第3コーナー")
    fourth_corner: int | None = Field(default=None, description="第4コーナー")


class RaceEvidenceItem(BaseModel):
    """1頭分の確定レース事実。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: str = Field(description="馬番")
    horse_name: str | None = Field(default=None, description="馬名")
    is_final_result: bool | None = None
    is_complete_result: bool | None = None
    result_status_code: str | None = None
    finish_position: FinishPositionFact = Field(description="着順または取消・除外状態")
    running_time: RunningTimeFact = Field(description="走破タイム")
    time_difference: TimeDifferenceFact = Field(description="勝ち馬とのタイム差")
    last_3f: float | None = Field(default=None, description="上がり3ハロン。秒")
    corner_positions: CornerPositionsFact = Field(description="各コーナー通過順位")


class RaceEvidenceCollectorOutput(BaseModel):
    """確定レースのラップと馬別事実。"""

    race_code: str = Field(description="レースコード")
    race_name: str | None = Field(default=None, description="レース名")
    track_code: str | None = Field(default=None, description="JRAトラックコード")
    distance: int | None = Field(default=None, description="距離。m")
    laps: RaceLaps | None = Field(default=None, description="レース全体のラップ")
    items: list[RaceEvidenceItem] = Field(description="馬別の確定事実")
    count: int = Field(description="馬別事実の件数")
    result_policy: str = "complete"


_SQL = """
WITH target_cards AS MATERIALIZED (
    SELECT DISTINCT ON (c.race_id, c.horse_number)
        c.race_id,
        c.horse_number,
        c.horse_name,
        c.abnormality_code,
        c.abnormality_name
    FROM jv_mart.race_cards AS c
    WHERE c.race_id = :race_code
      AND c.horse_number IS NOT NULL
    ORDER BY c.race_id, c.horse_number, c.horse_id
)
SELECT
    r.race_id AS race_code,
    r.race_name,
    r.track_code,
    r.distance_meters AS distance,
    r.first_three_furlongs_seconds AS front_3f,
    r.first_four_furlongs_seconds AS front_4f,
    r.last_three_furlongs_seconds AS back_3f,
    r.last_four_furlongs_seconds AS back_4f,
    COALESCE(
        (
            SELECT jsonb_agg(l.lap_seconds ORDER BY l.lap_number)
            FROM jv_data.race_laps AS l
            WHERE l.race_id = r.race_id
        ),
        '[]'::jsonb
    ) AS lap_times,
    c.horse_number,
    c.horse_name,
    c.abnormality_code,
    c.abnormality_name,
    rr.finish_position,
    rr.elapsed_seconds,
    rr.time_difference_seconds,
    rr.last_three_furlongs_seconds AS last_3f,
    rr.corner_1_position,
    rr.corner_2_position,
    rr.corner_3_position,
    rr.corner_4_position,
    rr.is_final_result,
    rr.is_complete_result,
    rr.is_starter,
    rr.is_finisher,
    rr.result_status_code
FROM jv_data.races AS r
JOIN target_cards AS c ON c.race_id = r.race_id
LEFT JOIN jv_mart.race_results AS rr
  ON rr.race_id = c.race_id
 AND rr.horse_number = c.horse_number
 AND (rr.is_final_result IS TRUE OR (:include_provisional AND rr.is_complete_result IS TRUE
      AND rr.result_status_code='provisional_complete'))
WHERE r.is_complete_result IS TRUE
  AND (r.result_status_code='official' OR (:include_provisional AND r.result_status_code='provisional_complete'))
ORDER BY c.horse_number
LIMIT :limit
"""


def _format_running_time(seconds: float | None) -> str | None:
    """秒値を分:秒.1へ変換する。"""

    if seconds is None or seconds < 0:
        return None
    minutes, remaining = divmod(round(seconds * 10), 600)
    return f"{minutes}:{remaining / 10:04.1f}"


def _raw_running_time(seconds: float | None) -> str | None:
    """秒値をJRAの ``MMSS`` (1/10秒) 表記へ戻す。

    read modelの走破時計は秒単位であり、公開するraw値は
    ``2:32.0`` を ``2320`` のように分・秒・十分の一秒で表す。
    秒へ単純に10倍した ``1520`` を返すと、利用側が別の時計形式と誤認する。
    """

    if seconds is None or seconds < 0:
        return None
    total_tenths = round(seconds * 10)
    minutes, remainder = divmod(total_tenths, 600)
    whole_seconds, tenths = divmod(remainder, 10)
    return f"{minutes}{whole_seconds:02d}{tenths}"


def _raw_time_difference(seconds: float | None) -> str | None:
    """秒差をJRAの符号付き十分の一秒表記へ戻す。"""

    if seconds is None:
        return None
    value = round(abs(seconds) * 10)
    sign = "+" if seconds >= 0 else "-"
    return f"{sign}{value:03d}"


def _is_withdrawn(row: dict[str, object]) -> bool:
    """出馬表・結果の状態から取消／除外を判定する。"""

    return runner_status(row) in {"withdrawn", "excluded"}


def _is_did_not_finish(row: dict[str, object]) -> bool:
    """実際に発走したが完走しなかった行を判定する。"""

    code = str(row.get("abnormality_code") or "")
    name = str(row.get("abnormality_name") or "")
    return (
        not _is_withdrawn(row)
        and row.get("is_starter") is True
        and (row.get("is_finisher") is False or code == "4" or "競走中止" in name)
    )


def _make_item(row: dict[str, object]) -> RaceEvidenceItem:
    """SQL行を馬別のネスト事実へ変換する。"""

    withdrawn = _is_withdrawn(row)
    administrative_status = runner_status(row)
    did_not_finish = administrative_status == "did_not_finish" or _is_did_not_finish(row)
    finish = int(row["finish_position"]) if row.get("finish_position") is not None else None
    elapsed = float(row["elapsed_seconds"]) if row.get("elapsed_seconds") is not None else None
    difference = float(row["time_difference_seconds"]) if row.get("time_difference_seconds") is not None else None
    finish_status = (
        administrative_status
        if withdrawn
        else "disqualified"
        if administrative_status == "disqualified"
        else "did_not_finish"
        if did_not_finish
        else "available"
        if finish is not None and row.get("is_final_result") is not False
        else "provisional"
        if finish is not None
        and row.get("is_complete_result") is True
        and row.get("result_status_code") == "provisional_complete"
        else "not_confirmed"
    )
    excluded = finish_status not in {"available", "provisional"}
    return RaceEvidenceItem(
        horse_number=f"{int(row['horse_number']):02d}",
        horse_name=row.get("horse_name"),
        is_final_result=row.get("is_final_result"),
        is_complete_result=row.get("is_complete_result"),
        result_status_code=row.get("result_status_code"),
        finish_position=FinishPositionFact(
            raw="除外"
            if administrative_status == "excluded"
            else "取消"
            if withdrawn
            else "失格"
            if administrative_status == "disqualified"
            else f"{finish:02d}"
            if finish is not None
            else None,
            value=finish if not excluded else None,
            status=finish_status,
            excluded_from_aggregates=excluded,
            excluded_reason=finish_status if excluded else None,
        ),
        running_time=RunningTimeFact(
            raw=_raw_running_time(elapsed),
            seconds=elapsed,
            formatted=_format_running_time(elapsed),
        ),
        time_difference=TimeDifferenceFact(
            raw=_raw_time_difference(difference),
            seconds=difference,
        ),
        last_3f=float(row["last_3f"]) if row.get("last_3f") is not None else None,
        corner_positions=CornerPositionsFact(
            first_corner=int(row["corner_1_position"]) if row.get("corner_1_position") is not None else None,
            second_corner=int(row["corner_2_position"]) if row.get("corner_2_position") is not None else None,
            third_corner=int(row["corner_3_position"]) if row.get("corner_3_position") is not None else None,
            fourth_corner=int(row["corner_4_position"]) if row.get("corner_4_position") is not None else None,
        ),
    )


def build_race_evidence_collector_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したrace_evidence_collector関数を作る。"""

    async def race_evidence_collector(
        race_code: RaceCode, result_scope: ResultScope = "complete"
    ) -> RaceEvidenceCollectorOutput:
        """対象レースのラップ・全頭揃った結果を取得する。速報は改訂可能と明示する。

        result_scope=officialで成績段階に限定する。予想の選択・買い目を固定してから使う。
        """

        rows = await fetch_rows(
            database,
            _SQL,
            {"race_code": race_code, "limit": 100, "include_provisional": include_provisional(result_scope)},
        )
        laps = None
        if rows:
            lap_row = rows[0]
            laps = RaceLaps(
                front_3f=float(lap_row["front_3f"]) if lap_row.get("front_3f") is not None else None,
                front_4f=float(lap_row["front_4f"]) if lap_row.get("front_4f") is not None else None,
                back_3f=float(lap_row["back_3f"]) if lap_row.get("back_3f") is not None else None,
                back_4f=float(lap_row["back_4f"]) if lap_row.get("back_4f") is not None else None,
                lap_times=[float(value) for value in lap_row.get("lap_times", [])],
                lap_segment_lengths_meters=lap_segment_lengths(lap_row.get("distance"), lap_row.get("track_code")),
                lap_windows={
                    "three": lap_window_comparison(lap_row.get("distance"), lap_row.get("track_code"), 3),
                    "four": lap_window_comparison(lap_row.get("distance"), lap_row.get("track_code"), 4),
                },
                front_three_segment_meters=opening_segment_meters(
                    lap_row.get("distance"), lap_row.get("track_code"), 3
                ),
                front_four_segment_meters=opening_segment_meters(
                    lap_row.get("distance"), lap_row.get("track_code"), 4
                ),
            )
        first = rows[0] if rows else {}
        items = [_make_item(row) for row in rows]
        return RaceEvidenceCollectorOutput(
            race_code=race_code,
            result_policy=result_scope,
            race_name=first.get("race_name"),
            track_code=first.get("track_code"),
            distance=int(first["distance"]) if first.get("distance") is not None else None,
            laps=laps,
            items=items,
            count=len(items),
        )

    return race_evidence_collector
