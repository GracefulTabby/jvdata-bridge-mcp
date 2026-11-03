"""過去日と当日の枠・脚質成績を、同条件の完全結果から集計する。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_boundary_sql, resolve_race_boundary
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.pace._normalization import normalized_running_style_sql

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class FrameStyleStatsItem(BaseModel):
    """枠・脚質別着順集計1件。"""

    model_config = ConfigDict(extra="forbid")

    frame_number: int | None = Field(default=None, description="枠番")
    running_style_code: str | None = Field(default=None, description="データソースの脚質コード")
    running_style: str = Field(description="正規化脚質")
    starts: int = Field(description="出走数")
    wins: int = Field(description="勝利数")
    top3_finishes: int = Field(description="3着以内数")
    provisional_starts: int = Field(default=0, description="改訂可能な完全速報の出走数")
    official_starts: int = Field(default=0, description="公式確定結果の出走数")
    finish_position_count: int = Field(default=0, description="平均着順の有効件数")
    average_finish_position: float | None = Field(default=None, description="平均着順")


class CourseFrameStyleStatsOutput(BaseModel):
    """対象条件の枠・脚質別着順集計。"""

    result_policy: str = "complete_official_or_provisional; provisional_can_be_revised"

    race_code: str
    analysis_at: str
    historical_stats: list[FrameStyleStatsItem] = Field(description="分析当日より前の枠・脚質別成績")
    today_stats: list[FrameStyleStatsItem] = Field(description="今日の対象・現在より前の完全結果による同条件成績")
    historical_race_count: int
    today_race_count: int
    history_start_date: str | None = None
    history_end_date: str | None = None
    today_race_codes: list[str] = Field(default_factory=list)
    cohort_policy: str = "history_before_analysis_day; today_separate; same_venue_distance_track; completed_starters"
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Same-day observations are a small selected sample, not proof of persistent track bias.",
            "Class, ability, pace and changes in ground conditions are not controlled by these raw counts.",
            "Running style describes recorded past races, not a promised target-race tactic.",
        ]
    )


_NORMALIZED_RUNNING_STYLE = normalized_running_style_sql("h.running_style_code")


_SQL = f"""
WITH target AS MATERIALIZED (
    SELECT venue_code, distance_meters, track_code, race_date
    FROM jv_data.races
    WHERE race_id = :race_code
), similar_races AS MATERIALIZED (
    SELECT r.race_id,r.race_date,
        CASE WHEN r.race_date=CAST(:today AS date) THEN 'today' ELSE 'history' END AS cohort
    FROM jv_data.races AS r
    CROSS JOIN target AS t
    WHERE r.venue_code = t.venue_code
      AND r.distance_meters = t.distance_meters
      AND r.track_code = t.track_code
      AND {before_boundary_sql("r")}
), per_race AS MATERIALIZED (
    SELECT
        r.race_id,r.race_date,r.cohort,
        stats.frame_number,
        stats.running_style_code,
        stats.running_style,
        stats.starts,
        stats.wins,
        stats.top3_finishes,
        stats.provisional_starts,stats.official_starts,
        stats.finish_position_count,
        stats.finish_position_sum
    FROM similar_races AS r
    CROSS JOIN LATERAL (
        SELECT
            c.frame_number,
            h.running_style_code,
            {_NORMALIZED_RUNNING_STYLE} AS running_style,
            COUNT(*) AS starts,
            COUNT(*) FILTER (WHERE rr.finish_position = 1) AS wins,
            COUNT(*) FILTER (WHERE rr.finish_position <= 3) AS top3_finishes,
            COUNT(*) FILTER (WHERE rr.result_status_code='provisional_complete') AS provisional_starts,
            COUNT(*) FILTER (WHERE rr.result_status_code='official') AS official_starts,
            COUNT(rr.finish_position) AS finish_position_count,
            SUM(rr.finish_position) AS finish_position_sum
        FROM jv_mart.race_cards AS c
        JOIN jv_mart.race_results AS rr ON rr.race_id = c.race_id AND rr.horse_id = c.horse_id
        LEFT JOIN jv_mart.horse_runs AS h ON h.race_id = c.race_id AND h.horse_id = c.horse_id
        WHERE c.race_id = r.race_id
          AND rr.is_complete_result IS TRUE AND rr.is_starter IS TRUE
          AND rr.result_status_code IN ('official','provisional_complete')
        GROUP BY c.frame_number, h.running_style_code, {_NORMALIZED_RUNNING_STYLE}
    ) AS stats
)
SELECT
    cohort,
    MIN(race_date)::text AS first_race_date,
    MAX(race_date)::text AS last_race_date,
    ARRAY_AGG(DISTINCT race_id ORDER BY race_id) AS race_codes,
    frame_number,
    running_style_code,
    running_style,
    COALESCE(SUM(starts), 0)::bigint AS starts,
    COALESCE(SUM(wins), 0)::bigint AS wins,
    COALESCE(SUM(top3_finishes), 0)::bigint AS top3_finishes,
    COALESCE(SUM(provisional_starts), 0)::bigint AS provisional_starts,
    COALESCE(SUM(official_starts), 0)::bigint AS official_starts,
    COALESCE(SUM(finish_position_count), 0)::bigint AS finish_position_count,
    ROUND(SUM(finish_position_sum)::numeric / NULLIF(SUM(finish_position_count), 0), 3) AS average_finish_position
FROM per_race
GROUP BY cohort, frame_number, running_style_code, running_style
ORDER BY cohort,frame_number, running_style_code
LIMIT :limit
"""


def build_course_frame_style_stats_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入した枠・脚質成績ツールを作る。"""

    async def course_frame_style_stats(race_code: RaceCode) -> CourseFrameStyleStatsOutput:
        """同じ競馬場・距離・走路の枠別・脚質別成績を、過去日と今日に分ける。

        今日の完全速報も状態を確認して含める。両集団は重複しない。
        少数の同日結果を恒常的な馬場バイアスや今回の勝率へ変換しない。

        Args:
            race_code: 条件を参照する対象レースのレースコード。

        Returns:
            枠番・脚質別の成績。
        """

        boundary = await resolve_race_boundary(database, race_code)
        rows = await fetch_rows(
            database, _SQL, {**boundary.parameters(), "today": boundary.analysis_at[:10], "limit": 1_000}
        )
        history = [r for r in rows if r["cohort"] == "history"]
        today = [r for r in rows if r["cohort"] == "today"]
        fields = set(FrameStyleStatsItem.model_fields)
        history_codes = {str(c) for r in history for c in r.get("race_codes", [])}
        today_codes = {str(c) for r in today for c in r.get("race_codes", [])}
        return CourseFrameStyleStatsOutput(
            race_code=race_code,
            analysis_at=boundary.analysis_at,
            historical_stats=[{k: v for k, v in r.items() if k in fields} for r in history],
            today_stats=[{k: v for k, v in r.items() if k in fields} for r in today],
            historical_race_count=len(history_codes),
            today_race_count=len(today_codes),
            history_start_date=min((str(r["first_race_date"]) for r in history), default=None),
            history_end_date=max((str(r["last_race_date"]) for r in history), default=None),
            today_race_codes=sorted(today_codes),
        )

    return course_frame_style_stats
