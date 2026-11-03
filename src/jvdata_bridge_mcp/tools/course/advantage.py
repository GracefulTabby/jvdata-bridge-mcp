"""コース優位性を直接SQLで集計するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import RaceBoundary, RaceCode, before_boundary_sql, resolve_race_boundary
from jvdata_bridge_mcp.tools._runtime import fetch_rows

RacecourseCode = Annotated[str, Field(description="競馬場コード")]
Distance = Annotated[int, Field(description="距離。メートル単位")]
TrackCode = Annotated[str, Field(description="走路コード")]


class CourseAdvantageItem(BaseModel):
    """競馬場・距離・走路条件の成績優位性1件。"""

    model_config = ConfigDict(extra="forbid")

    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    distance_meters: int | None = Field(default=None, description="距離。m")
    track_code: str | None = Field(default=None, description="走路コード")
    race_count: int = Field(description="対象レース数")
    starts: int = Field(description="出走数")
    wins: int = Field(description="勝利数")
    top3_finishes: int = Field(description="3着以内数")
    average_finish_position: float | None = Field(default=None, description="平均着順")
    average_win_odds: float | None = Field(default=None, description="平均単勝オッズ")
    race_code: str | None = Field(default=None, description="基準レースコード")


class CourseAdvantageAnalyzerOutput(BaseModel):
    """競馬場・距離・走路条件の成績優位性。"""

    result_policy: str = "complete_official_or_provisional; provisional_can_be_revised"

    advantages: list[CourseAdvantageItem] = Field(description="コース条件別の優位性")
    boundary: RaceBoundary | None = None


_SQL = f"""
SELECT
    r.venue_code AS racecourse_code,
    r.distance_meters,
    r.track_code,
    COUNT(DISTINCT r.race_id) AS race_count,
    COUNT(rr.horse_id) AS starts,
    COUNT(rr.horse_id) FILTER (WHERE rr.finish_position = 1) AS wins,
    COUNT(rr.horse_id) FILTER (WHERE rr.finish_position <= 3) AS top3_finishes,
    ROUND(AVG(rr.finish_position)::numeric, 3) AS average_finish_position,
    ROUND(AVG(rr.final_win_odds)::numeric, 3) AS average_win_odds,
    NULLIF(:race_code, '') AS race_code
FROM jv_data.races AS r
JOIN jv_mart.race_results AS rr ON rr.race_id = r.race_id
WHERE r.venue_code = :racecourse_code
  AND rr.is_complete_result IS TRUE AND rr.is_starter IS TRUE
  AND r.distance_meters = :distance
  AND (:track_code = '' OR r.track_code = :track_code)
  AND {before_boundary_sql("r")}
GROUP BY r.venue_code, r.distance_meters, r.track_code
ORDER BY top3_finishes DESC, average_finish_position ASC NULLS LAST
LIMIT :limit
"""


def build_course_advantage_analyzer_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したcourse_advantage_analyzer関数を作る。"""

    async def course_advantage_analyzer(
        racecourse_code: RacecourseCode,
        distance: Distance,
        race_code: RaceCode = "",
        track_code: TrackCode = "",
    ) -> CourseAdvantageAnalyzerOutput:
        """競馬場・距離条件のコース優位性を分析する。

        指定条件の過去結果から出走、勝利、複勝圏、平均着順を集計する。

        Args:
            racecourse_code: 競馬場コード。
            distance: 距離。メートル単位。
            race_code: 対象レース。取得範囲は発走時刻と現在時刻から自動で決まる。
            track_code: 走路コード。空文字は全走路。

        Returns:
            コース条件別成績。
        """

        boundary = await resolve_race_boundary(database, race_code)
        rows = await fetch_rows(
            database,
            _SQL,
            {
                "racecourse_code": racecourse_code,
                "distance": distance,
                **boundary.parameters(),
                "track_code": track_code,
                "limit": 100,
            },
            disable_jit=True,
        )
        return CourseAdvantageAnalyzerOutput(advantages=rows, boundary=boundary)

    return course_advantage_analyzer
