"""レース検索を直接SQLで実行するMCPツール。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._observation_scope import now_jst
from jvdata_bridge_mcp.tools._runtime import fetch_rows

Grade = Annotated[str, Field(description="レース格。G1、G2、G3など")]
Limit = Annotated[int, Field(description="返却する最大件数")]
RaceName = Annotated[str, Field(description="レース名による部分一致検索条件")]
RacecourseName = Annotated[str, Field(description="競馬場名による絞り込み条件")]
Year = Annotated[int, Field(description="開催年。0の場合は年で絞り込まない")]


class RaceSearchItem(BaseModel):
    """検索条件に一致したレースの1件。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    race_date: str | None = Field(default=None, description="開催日")
    year: int | None = Field(default=None, description="開催年")
    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    racecourse_name: str | None = Field(default=None, description="競馬場名")
    race_number: int | None = Field(default=None, description="レース番号")
    scheduled_start_at: str | None = None
    declared_entry_count: int | None = None
    timezone: str = "Asia/Tokyo"
    race_name: str | None = Field(default=None, description="レース名")
    race_condition_name: str | None = Field(default=None, description="レース条件名")
    grade_code: str | None = Field(default=None, description="グレードコード")
    grade_name: str | None = Field(default=None, description="グレード名")
    distance: int | None = Field(default=None, description="距離。m")
    track_code: str | None = Field(default=None, description="走路コード")
    track_name: str | None = Field(default=None, description="走路名")
    starters: int | None = Field(default=None, description="出走頭数")


class SearchRacesOutput(BaseModel):
    """条件に一致したレース一覧。"""

    races: list[RaceSearchItem] = Field(description="条件に一致したレース一覧")


_SQL = """
SELECT
    r.race_id AS race_code,
    r.race_date,
    r.race_year AS year,
    r.venue_code AS racecourse_code,
    r.venue_name AS racecourse_name,
    r.race_number,
    r.race_name,
    r.scheduled_start_at, r.registered_horses AS declared_entry_count,
    r.race_condition_name,
    r.grade_code,
    r.grade_name,
    r.distance_meters AS distance,
    r.track_code,
    r.track_name,
    r.starters
FROM jv_data.races AS r
WHERE (:grade = '' OR r.grade_code = :grade OR r.grade_name ILIKE '%' || :grade || '%')
  AND (:race_name = '' OR r.race_name ILIKE '%' || :race_name || '%')
  AND (:racecourse_name = '' OR r.venue_name ILIKE '%' || :racecourse_name || '%')
  AND (:year = 0 OR r.race_year = :year)
  AND (:race_date = '' OR r.race_date = CAST(NULLIF(:race_date, '') AS date))
  AND (NOT :upcoming_only OR r.scheduled_start_at > CAST(:analysis_local AS timestamp))
ORDER BY r.race_date DESC, r.venue_code, r.race_number
LIMIT :limit
"""


def build_search_races_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したsearch_races関数を作る。"""

    async def search_races(
        grade: Grade = "",
        limit: Limit = 30,
        race_name: RaceName = "",
        racecourse_name: RacecourseName = "",
        year: Year = 0,
        race_date: Annotated[str, Field(description="開催日 YYYY-MM-DD、today、tomorrow。省略は全日付")] = "",
        upcoming_only: Annotated[bool, Field(description="未発走に限定。日付省略時は今日")] = False,
    ) -> SearchRacesOutput:
        """条件を組み合わせてレースを検索する。

        レース名、競馬場名、開催年、グレードをAND条件で絞り込み、開催日の新しい順に返す。
        race_date=today、upcoming_only=trueで今日これからの発走予定と登録頭数を取得できる。

        Args:
            grade: G1、G2、G3などのグレードまたは名称。
            limit: 返却する最大件数。
            race_name: レース名の部分一致条件。
            racecourse_name: 競馬場名の部分一致条件。
            year: 開催年。0の場合は指定しない。

        Returns:
            条件に一致したレースの行一覧。
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
                "race_name": race_name,
                "racecourse_name": racecourse_name,
                "year": year,
            },
        )
        return SearchRacesOutput(races=rows)

    return search_races
