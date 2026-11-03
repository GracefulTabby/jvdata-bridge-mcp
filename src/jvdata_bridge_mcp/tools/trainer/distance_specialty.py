"""調教師の距離・走路別成績を直接SQLで集計するMCPツール。"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._people import person_history_sql, person_period
from jvdata_bridge_mcp.tools._race_boundary import RaceBoundary, RaceCode, period_dates, resolve_race_boundary
from jvdata_bridge_mcp.tools._runtime import fetch_rows

DistanceCategory = Annotated[str, Field(description="距離区分")]
TrackType = Annotated[str, Field(description="芝・ダートなどの走路種別")]
TrainerCode = Annotated[str, Field(description="調教師コード")]

_TRACK_TYPE_TO_SURFACE_CODE = {
    "芝": "turf",
    "ダート": "dirt",
}
_DISTANCE_CATEGORY_TO_BAND_SUFFIX = {
    # people_statisticsの距離集計は3帯で、表示カテゴリは4区分である。
    # 1600m以下に含まれるマイルは、同じ帯を使うことを出力説明で明示する。
    "短距離": "1600_or_less",
    "マイル": "1600_or_less",
    "中距離": "1601_2200",
    "長距離": "2201_or_more",
}


class TrainerDistanceSpecialtyItem(BaseModel):
    """調教師の距離・走路別成績1件。"""

    model_config = ConfigDict(extra="forbid")

    trainer_code: str = Field(description="調教師コード")
    trainer_name: str | None = Field(default=None, description="調教師名")
    distance_band_code: str | None = Field(default=None, description="距離帯コード")
    track_type: str | None = Field(default=None, description="走路種別")
    provisional_starts: int = 0
    starts: int = Field(description="出走数")
    wins: int = Field(description="勝利数")
    top3_finishes: int = Field(description="3着以内数")
    flat_prize_money_yen: int | None = Field(default=None, description="平地賞金。円")


class AnalyzeTrainerDistanceSpecialtyOutput(BaseModel):
    """調教師の距離・走路別成績。"""

    aggregation_scope: str = "single_period:coarse_distance_band; mile_includes_all_1600_or_less"
    period: str = "career"
    boundary: RaceBoundary | None = None
    specialties: list[TrainerDistanceSpecialtyItem] = Field(description="距離・走路別調教師成績")


_SQL = """
SELECT
    s.person_id AS trainer_code,
    s.person_name AS trainer_name,
    s.distance_band_code,
    s.surface_code AS track_type,
    SUM(s.finish_count) AS starts,
    SUM(s.finish_count) FILTER (WHERE s.finish_index = 0) AS wins,
    SUM(s.finish_count) FILTER (WHERE s.finish_index <= 2) AS top3_finishes,
    NULL::bigint AS flat_prize_money_yen
FROM jv_mart.people_statistics AS s
WHERE s.person_type = 'trainer'
  AND s.person_id = :trainer_code
  AND s.period_index = :period_index
  AND s.distance_band_code = :distance_band_code
  AND s.surface_code = :surface_code
GROUP BY s.person_id, s.person_name, s.distance_band_code, s.surface_code
ORDER BY wins DESC NULLS LAST, starts DESC
LIMIT :limit
"""


def build_analyze_trainer_distance_specialty_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したanalyze_trainer_distance_specialty関数を作る。"""

    async def analyze_trainer_distance_specialty(
        distance_category: DistanceCategory,
        track_type: TrackType,
        trainer_code: TrainerCode,
        period: str = "career",
        race_code: RaceCode = "",
    ) -> AnalyzeTrainerDistanceSpecialtyOutput:
        """調教師の距離・走路別成績を分析する。

        Args:
            distance_category: 距離区分。
            track_type: 走路種別。
            trainer_code: 調教師コード。

        Returns:
            調教師の条件別成績。
        """

        normalized_track_type = str(track_type).strip()
        normalized_distance_category = str(distance_category).strip()
        try:
            surface_code = _TRACK_TYPE_TO_SURFACE_CODE[normalized_track_type]
        except KeyError as exc:
            raise ValueError("track_type must be one of: 芝, ダート") from exc
        try:
            distance_band_suffix = _DISTANCE_CATEGORY_TO_BAND_SUFFIX[normalized_distance_category]
        except KeyError as exc:
            raise ValueError("distance_category must be one of: 短距離, マイル, 中距離, 長距離") from exc

        period_index = person_period(period)
        boundary = await resolve_race_boundary(database, race_code)
        if race_code:
            start, end = period_dates(date.fromisoformat(boundary.race_date), period)
            minimum, maximum = {"1600_or_less": (0, 1600), "1601_2200": (1601, 2200), "2201_or_more": (2201, 0)}[
                distance_band_suffix
            ]
            rows = await fetch_rows(
                database,
                person_history_sql("trainer"),
                {
                    "person_id": trainer_code,
                    **boundary.parameters(),
                    "include_provisional": True,
                    "start_date": start,
                    "end_date": end,
                    "venue": "",
                    "surface": surface_code,
                    "distance_min": minimum,
                    "distance_max": maximum,
                    "is_graded": False,
                },
                disable_jit=True,
            )
            allowed = set(TrainerDistanceSpecialtyItem.model_fields)
            rows = [{k: v for k, v in row.items() if k in allowed} for row in rows]
            for row in rows:
                row["distance_band_code"] = f"{surface_code}_{distance_band_suffix}"
            scope = "complete_stored_JRA_runs_before_target_and_now:coarse_distance_band; provisional_can_be_revised; mile_includes_all_1600_or_less"
        else:
            rows = await fetch_rows(
                database,
                _SQL,
                {
                    "distance_band_code": f"{surface_code}_{distance_band_suffix}",
                    "surface_code": surface_code,
                    "trainer_code": trainer_code,
                    "period_index": period_index,
                    "limit": 100,
                },
            )
            scope = "single_period:coarse_distance_band; mile_includes_all_1600_or_less"
        return AnalyzeTrainerDistanceSpecialtyOutput(
            specialties=rows, period=period, aggregation_scope=scope, boundary=boundary
        )

    return analyze_trainer_distance_specialty
