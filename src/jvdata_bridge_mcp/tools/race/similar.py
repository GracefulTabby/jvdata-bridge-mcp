"""類似レース検索を直接SQLで実行するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.race.track_condition import build_track_condition_summary_tool

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]
Limit = Annotated[int, Field(description="返却する最大件数")]
MaxEntryDifference = Annotated[int, Field(description="出走頭数差の許容値")]
SameDistance = Annotated[bool, Field(description="距離が同じレースに限定するか")]
SameGrade = Annotated[bool, Field(description="グレードが同じレースに限定するか")]
SameTrackCondition = Annotated[bool, Field(description="馬場状態が同じレースに限定するか")]
SameWeather = Annotated[bool, Field(description="天候が同じレースに限定するか")]


class SimilarRaceItem(BaseModel):
    """対象レースと条件が近い過去レースの1件。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    race_date: str | None = Field(default=None, description="開催日")
    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    racecourse_name: str | None = Field(default=None, description="競馬場名")
    race_number: int | None = Field(default=None, description="レース番号")
    race_name: str | None = Field(default=None, description="レース名")
    grade_code: str | None = Field(default=None, description="グレードコード")
    grade_name: str | None = Field(default=None, description="グレード名")
    distance: int | None = Field(default=None, description="距離。m")
    track_code: str | None = Field(default=None, description="走路コード")
    track_name: str | None = Field(default=None, description="走路名")
    weather_name: str | None = Field(default=None, description="天候")
    turf_condition_name: str | None = Field(default=None, description="芝馬場状態")
    dirt_condition_name: str | None = Field(default=None, description="ダート馬場状態")
    registered_horses: int | None = Field(default=None, description="登録頭数")
    starters: int | None = Field(default=None, description="出走頭数")
    entry_count_difference: int | None = Field(default=None, description="出走頭数差")


class AnalyzeSimilarRacesOutput(BaseModel):
    """対象レースと条件が近い過去レースの一覧。"""

    races: list[SimilarRaceItem] = Field(description="類似レース一覧")


_SQL = f"""
WITH target AS (
    SELECT race_id,race_date,venue_code,track_code,distance_meters,grade_code,registered_horses
    FROM jv_data.races
    WHERE race_id = :race_code
)
SELECT
    r.race_id AS race_code,
    r.race_date,
    r.venue_code AS racecourse_code,
    r.venue_name AS racecourse_name,
    r.race_number,
    r.race_name,
    r.grade_code,
    r.grade_name,
    r.distance_meters AS distance,
    r.track_code,
    r.track_name,
    r.weather_name,
    r.turf_condition_name,
    r.dirt_condition_name,
    r.registered_horses,
    r.starters,
    ABS(
        COALESCE(r.starters, r.registered_horses, 0)
        - COALESCE(t.registered_horses, 0)
    ) AS entry_count_difference
FROM jv_data.races AS r
CROSS JOIN target AS t
WHERE r.race_id <> t.race_id
  AND {before_target_race_sql("r", "t.race_date")}
  AND r.venue_code = t.venue_code
  AND r.track_code = t.track_code
  AND COALESCE(r.starters, 0) > 0
  AND r.is_complete_result IS TRUE
  AND (:max_entry_difference = 0 OR ABS(COALESCE(r.starters, r.registered_horses, 0) - COALESCE(t.registered_horses, 0)) <= :max_entry_difference)
  AND (NOT :same_distance OR r.distance_meters = t.distance_meters)
  AND (NOT :same_grade OR r.grade_code = t.grade_code)
  AND (
      NOT :same_track_condition
      OR CASE WHEN t.track_code LIKE '1%' THEN r.turf_condition_code ELSE r.dirt_condition_code END = :ground_code
  )
  AND (NOT :same_weather OR r.weather_code = :weather_code)
ORDER BY entry_count_difference, r.race_date DESC
LIMIT :limit
"""


def build_analyze_similar_races_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したanalyze_similar_races関数を作る。"""

    async def analyze_similar_races(
        race_code: RaceCode,
        limit: Limit = 30,
        max_entry_difference: MaxEntryDifference = 0,
        same_distance: SameDistance = True,
        same_grade: SameGrade = True,
        same_track_condition: SameTrackCondition = False,
        same_weather: SameWeather = False,
    ) -> AnalyzeSimilarRacesOutput:
        """対象レースと条件が近い過去レースを検索する。

        距離、グレード、馬場状態、天候、出走頭数差を個別に指定して比較し、
        対象レースより前のレースを近さ順に返す。

        Args:
            race_code: 比較基準となるレースコード。
            limit: 返却する最大件数。
            max_entry_difference: 出走頭数差の許容値。0の場合は制限しない。
            same_distance: 距離を一致条件にするか。
            same_grade: グレードを一致条件にするか。
            same_track_condition: 馬場状態を一致条件にするか。
            same_weather: 天候を一致条件にするか。

        Returns:
            条件の近い過去レース。
        """

        ground_code, weather_code = None, None
        if same_track_condition or same_weather:
            observations = await build_track_condition_summary_tool(database)(race_code)
            condition = observations.conditions[0] if observations.conditions else None
            if condition is None or condition.observation_date != condition.race_date:
                raise ValueError(
                    "same-day weather/ground observation is unavailable; disable the corresponding filter"
                )
            ground_code = (
                condition.turf_condition_code
                if str(condition.track_code).startswith("1")
                else condition.dirt_condition_code
            )
            weather_code = condition.weather_code
            if (same_track_condition and not ground_code) or (same_weather and not weather_code):
                raise ValueError("requested weather/ground filter has no observed value")
        rows = await fetch_rows(
            database,
            _SQL,
            {
                "race_code": race_code,
                "limit": max(1, min(limit, 1_000)),
                "max_entry_difference": max(0, max_entry_difference),
                "same_distance": same_distance,
                "same_grade": same_grade,
                "same_track_condition": same_track_condition,
                "same_weather": same_weather,
                "ground_code": ground_code,
                "weather_code": weather_code,
            },
        )
        return AnalyzeSimilarRacesOutput(races=rows)

    return analyze_similar_races
