"""重賞レース検索を直接SQLで実行するMCPツール。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._observation_scope import now_jst
from jvdata_bridge_mcp.tools._runtime import fetch_rows

Grade = Annotated[str, Field(description="G1、G2、G3、またはall")]
Limit = Annotated[int, Field(description="返却する最大件数")]
RacecourseName = Annotated[str, Field(description="競馬場名による絞り込み条件")]
Year = Annotated[int, Field(description="開催年。0の場合は年で絞り込まない")]


class GradedRaceItem(BaseModel):
    """重賞レースの1件。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    race_date: str | None = Field(default=None, description="開催日")
    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    racecourse_name: str | None = Field(default=None, description="競馬場名")
    race_number: int | None = Field(default=None, description="レース番号")
    scheduled_start_at: str | None = None
    declared_entry_count: int | None = None
    timezone: str = "Asia/Tokyo"
    race_name: str | None = Field(default=None, description="レース名")
    grade_code: str | None = Field(default=None, description="グレードコード")
    grade_name: str | None = Field(default=None, description="グレード名")
    distance: int | None = Field(default=None, description="距離。m")
    track_code: str | None = Field(default=None, description="走路コード")
    track_name: str | None = Field(default=None, description="走路名")


class GetGradedRacesOutput(BaseModel):
    """重賞レースの検索結果。"""

    races: list[GradedRaceItem] = Field(description="重賞レース一覧")


_SQL = """
SELECT
    r.race_id AS race_code,
    r.race_date,
    r.venue_code AS racecourse_code,
    r.venue_name AS racecourse_name,
    r.race_number,
    r.race_name,
    r.scheduled_start_at, r.registered_horses AS declared_entry_count,
    r.grade_code,
    r.grade_name,
    r.distance_meters AS distance,
    r.track_code,
    r.track_name
FROM jv_data.races AS r
WHERE (:grade = 'all' OR r.grade_code = :grade OR r.grade_name ILIKE '%' || :grade || '%')
  AND (:racecourse_name = '' OR r.venue_name ILIKE '%' || :racecourse_name || '%')
  AND (:year = 0 OR r.race_year = :year)
  AND (
      :grade <> 'all'
      OR r.grade_code IN ('A','B','C','F','G','H')
      OR r.grade_name ILIKE 'G%'
  )
  AND (:race_date = '' OR r.race_date = CAST(NULLIF(:race_date, '') AS date))
  AND (NOT :upcoming_only OR r.scheduled_start_at > CAST(:analysis_local AS timestamp))
ORDER BY r.race_date DESC, r.venue_code, r.race_number
LIMIT :limit
"""


def build_get_graded_races_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したget_graded_races関数を作る。"""

    async def get_graded_races(
        grade: Grade = "all",
        limit: Limit = 50,
        racecourse_name: RacecourseName = "",
        year: Year = 0,
        race_date: Annotated[str, Field(description="開催日 YYYY-MM-DD、today、tomorrow。省略は全日付")] = "",
        upcoming_only: Annotated[bool, Field(description="未発走に限定。日付省略時は今日")] = False,
    ) -> GetGradedRacesOutput:
        """重賞レースを検索する。

        gradeがallの場合はG1からG3までを対象にし、競馬場と開催年を追加条件にできる。

        Args:
            grade: G1、G2、G3、またはall。
            limit: 返却する最大件数。
            racecourse_name: 競馬場名の部分一致条件。
            year: 開催年。0の場合は指定しない。

        Returns:
            条件に一致した重賞レースの行一覧。
        """

        now = now_jst()
        if race_date in {"today", "tomorrow"}:
            race_date = (now.date() + timedelta(days=race_date == "tomorrow")).isoformat()
        elif race_date:
            race_date = date.fromisoformat(race_date).isoformat()
        elif upcoming_only:
            race_date = now.date().isoformat()
        grade = {
            "G1": "A",
            "GI": "A",
            "G2": "B",
            "GII": "B",
            "G3": "C",
            "GIII": "C",
            "J.G1": "F",
            "J.G2": "G",
            "J.G3": "H",
            "JG1": "F",
            "JG2": "G",
            "JG3": "H",
        }.get(grade.upper(), grade)
        rows = await fetch_rows(
            database,
            _SQL,
            {
                "grade": grade,
                "race_date": race_date,
                "upcoming_only": upcoming_only,
                "analysis_local": now.replace(tzinfo=None).isoformat(),
                "limit": max(1, min(limit, 1_000)),
                "racecourse_name": racecourse_name,
                "year": year,
            },
        )
        return GetGradedRacesOutput(races=rows)

    return get_graded_races
