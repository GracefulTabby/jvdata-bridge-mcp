"""競馬場・距離の傾向を直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import RaceBoundary, RaceCode, before_boundary_sql, resolve_race_boundary
from jvdata_bridge_mcp.tools._runtime import fetch_rows

RacecourseCode = Annotated[str, Field(description="競馬場コード")]
Distance = Annotated[int, Field(description="距離。メートル単位")]
TrackCode = Annotated[str, Field(description="走路コード")]


class CourseDistanceInsightItem(BaseModel):
    """競馬場・距離・走路別のレース傾向1件。"""

    model_config = ConfigDict(extra="forbid")

    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    racecourse_name: str | None = Field(default=None, description="競馬場名")
    distance_meters: int | None = Field(default=None, description="距離。m")
    track_code: str | None = Field(default=None, description="走路コード")
    track_name: str | None = Field(default=None, description="走路名")
    evidence_start_date: str | None = None
    evidence_end_date: str | None = None
    race_count: int = Field(description="対象レース数")
    average_first_three_furlongs: float | None = Field(default=None, description="平均前半3ハロン。秒")
    average_last_three_furlongs: float | None = Field(default=None, description="平均上がり3ハロン。秒")
    average_starters: float | None = Field(default=None, description="平均出走頭数")


class CourseDistanceInsightOutput(BaseModel):
    """競馬場・距離・走路別のレース傾向。"""

    result_policy: str = "complete_official_or_provisional; provisional_can_be_revised"

    insights: list[CourseDistanceInsightItem] = Field(description="距離別コース傾向")
    boundary: RaceBoundary | None = None


_SQL = f"""
SELECT
    r.venue_code AS racecourse_code,
    r.venue_name AS racecourse_name,
    r.distance_meters,
    r.track_code,
    r.track_name,
    COUNT(DISTINCT r.race_id) AS race_count,
    MIN(r.race_date) AS evidence_start_date,MAX(r.race_date) AS evidence_end_date,
    AVG(r.first_three_furlongs_seconds) AS average_first_three_furlongs,
    AVG(r.last_three_furlongs_seconds) AS average_last_three_furlongs,
    AVG(r.starters) AS average_starters
FROM jv_data.races AS r
WHERE r.venue_code = :racecourse_code
  AND r.distance_meters = :distance
  AND (:track_code = '' OR r.track_code = :track_code)
  AND r.is_complete_result IS TRUE
  AND {before_boundary_sql("r")}
GROUP BY r.venue_code, r.venue_name, r.distance_meters, r.track_code, r.track_name
"""


def build_course_distance_insight_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したcourse_distance_insight関数を作る。"""

    async def course_distance_insight(
        racecourse_code: RacecourseCode,
        distance: Distance,
        track_code: TrackCode = "",
        race_code: RaceCode = "",
    ) -> CourseDistanceInsightOutput:
        """競馬場・距離に特化したコース傾向を取得する。

        レース数、序盤・終盤時計、出走頭数を返す。

        Args:
            racecourse_code: 競馬場コード。
            distance: 距離。メートル単位。
            track_code: 走路コード。空文字は全走路。

        Returns:
            コース条件のペース傾向。
        """

        boundary = await resolve_race_boundary(database, race_code)
        rows = await fetch_rows(
            database,
            _SQL,
            {
                "racecourse_code": racecourse_code,
                "distance": distance,
                "track_code": track_code,
                "limit": 100,
                **boundary.parameters(),
            },
            disable_jit=True,
        )
        return CourseDistanceInsightOutput(insights=rows, boundary=boundary)

    return course_distance_insight
