"""指定条件の騎手成績を直接SQLで集計するMCPツール。"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._people import person_history_sql, surface_code, venue_code
from jvdata_bridge_mcp.tools._race_boundary import RaceBoundary, RaceCode, period_dates, resolve_race_boundary
from jvdata_bridge_mcp.tools._runtime import fetch_rows

JockeyCode = Annotated[str, Field(description="騎手コード")]
DistanceMax = Annotated[int, Field(description="対象距離の上限。0の場合は指定しない")]
DistanceMin = Annotated[int, Field(description="対象距離の下限。0の場合は指定しない")]
IsGraded = Annotated[bool, Field(description="重賞に限定するか")]
RacecourseName = Annotated[str, Field(description="競馬場名による絞り込み条件")]
TrackType = Annotated[str, Field(description="芝・ダートなどの走路種別")]


class JockeyConditionStatsItem(BaseModel):
    """指定条件における騎手成績1件。"""

    model_config = ConfigDict(extra="forbid")

    jockey_code: str = Field(description="騎手コード")
    jockey_name: str | None = Field(default=None, description="騎手名")
    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    track_type: str | None = Field(default=None, description="走路種別")
    distance_band_code: str | None = Field(default=None, description="距離帯コード")
    evidence_start_date: str | None = None
    evidence_end_date: str | None = None
    non_finishers: int = 0
    provisional_starts: int = 0
    starts: int = Field(description="出走数")
    wins: int = Field(description="勝利数")
    top3_finishes: int = Field(description="3着以内数")
    flat_prize_money_yen: int | None = Field(default=None, description="平地賞金。円")


class GetJockeyStatsOutput(BaseModel):
    """指定条件における騎手成績。"""

    aggregation_scope: str = "complete_stored_jra_runs_before_target_and_now; provisional_can_be_revised"
    period: str = "career"
    boundary: RaceBoundary | None = None
    statistics: list[JockeyConditionStatsItem] = Field(description="指定条件の騎手成績")


_SQL = person_history_sql("jockey")


def build_get_jockey_stats_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したget_jockey_stats関数を作る。"""

    async def get_jockey_stats(
        jockey_code: JockeyCode,
        distance_max: DistanceMax = 0,
        distance_min: DistanceMin = 0,
        is_graded: IsGraded = False,
        racecourse_name: RacecourseName = "",
        track_type: TrackType = "",
        period: Annotated[str, Field(description="career/current_year/previous_year")] = "career",
        race_code: RaceCode = "",
    ) -> GetJockeyStatsOutput:
        """指定条件における騎手成績を集計する。

        全頭揃った実出走を数える。距離はメートルの交差条件。
        対象レースと現在時刻から範囲を決め、当日の先行レースの完全速報も含める。
        原票の年度成績と累計を足さない。収録外の成績は含まれない。

        Args:
            jockey_code: 騎手コード。
            distance_max: 対象距離の上限。0の場合は指定しない。
            distance_min: 対象距離の下限。0の場合は指定しない。
            is_graded: 重賞条件に限定するか。
            racecourse_name: 競馬場名またはコード。
            track_type: 芝・ダートなどの走路。

        Returns:
            条件別の騎手成績。
        """

        boundary = await resolve_race_boundary(database, race_code)
        start_date, end_date = period_dates(
            date.fromisoformat(boundary.race_date or boundary.analysis_at[:10]), period
        )
        if distance_min < 0 or distance_max < 0 or (distance_max and distance_max < distance_min):
            raise ValueError("distance range must be nonnegative and ordered")
        rows = await fetch_rows(
            database,
            _SQL,
            {
                "person_id": jockey_code,
                **boundary.parameters(),
                "include_provisional": True,
                "start_date": start_date,
                "end_date": end_date,
                "distance_max": max(0, distance_max),
                "distance_min": max(0, distance_min),
                "is_graded": is_graded,
                "venue": venue_code(racecourse_name),
                "surface": surface_code(track_type),
                "limit": 1_000,
            },
            disable_jit=True,
        )
        return GetJockeyStatsOutput(
            statistics=rows,
            period=period,
            boundary=boundary,
        )

    return get_jockey_stats
