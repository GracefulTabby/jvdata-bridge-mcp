"""騎手戦術とペースの関係を直接SQLで集計するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.pace._normalization import normalized_running_style_sql

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]
ReferenceRaces = Annotated[int, Field(description="参照する過去レース数")]


class JockeyTacticsItem(BaseModel):
    """対象レースの騎手別戦術傾向1件。"""

    model_config = ConfigDict(extra="forbid")

    jockey_code: str = Field(description="騎手コード")
    jockey_name: str | None = Field(default=None, description="騎手名")
    reference_starts: int = Field(description="参照出走数")
    forward_rides: int = Field(description="前方脚質騎乗数")
    closing_rides: int = Field(description="差し・追込騎乗数")
    average_first_corner_position: float | None = Field(default=None, description="平均1コーナー順位")
    average_finish_position: float | None = Field(default=None, description="平均着順")


class RacePaceJockeyTacticsOutput(BaseModel):
    """対象レースの騎手別戦術傾向。"""

    result_policy: str = "complete_official_or_provisional; provisional_can_be_revised"

    tactics: list[JockeyTacticsItem] = Field(description="騎手別戦術傾向")


_NORMALIZED_RUNNING_STYLE = normalized_running_style_sql("running_style_code")


_SQL = f"""
WITH race_context AS MATERIALIZED (
    SELECT race_date
    FROM jv_data.races
    WHERE race_id = :race_code
), target AS (
    SELECT DISTINCT c.jockey_id, c.jockey_name, r.race_date
    FROM jv_mart.race_cards AS c
    CROSS JOIN race_context AS r
    WHERE c.race_id = :race_code

), history AS (
    SELECT
        e.jockey_id,
        e.jockey_name,
        rr.running_style_code,
        rr.corner_1_position,
        rr.finish_position,
        ROW_NUMBER() OVER (PARTITION BY e.jockey_id ORDER BY e.race_date DESC, e.race_id DESC) AS rn
    FROM target AS t
    JOIN jv_mart.entry_performances AS e ON e.jockey_id = t.jockey_id AND {before_target_race_sql("e", "t.race_date")}
    JOIN jv_mart.race_results AS rr ON rr.race_id = e.race_id AND rr.horse_id = e.horse_id
    WHERE (rr.is_final_result IS TRUE OR rr.result_status_code='provisional_complete')
      AND rr.is_complete_result IS TRUE AND rr.is_starter IS TRUE
)
SELECT
    jockey_id AS jockey_code,
    jockey_name,
    COUNT(*) AS reference_starts,
    COUNT(*) FILTER (WHERE {_NORMALIZED_RUNNING_STYLE} IN ('front_runner', 'stalker')) AS forward_rides,
    COUNT(*) FILTER (WHERE {_NORMALIZED_RUNNING_STYLE} IN ('closer', 'deep_closer')) AS closing_rides,
    ROUND(AVG(corner_1_position)::numeric, 3) AS average_first_corner_position,
    ROUND(AVG(finish_position)::numeric, 3) AS average_finish_position
FROM history
WHERE rn <= :reference_races
GROUP BY jockey_id, jockey_name
ORDER BY reference_starts DESC, average_finish_position ASC NULLS LAST
LIMIT :limit
"""


def build_race_pace_jockey_tactics_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したrace_pace_jockey_tactics関数を作る。"""

    async def race_pace_jockey_tactics(
        race_code: RaceCode,
        reference_races: ReferenceRaces = 80,
    ) -> RacePaceJockeyTacticsOutput:
        """対象レースの騎手戦術とペース関係を分析する。

        対象レースの騎手について、指定した過去走数の脚質と1コーナー位置を集計する。

        Args:
            race_code: 対象レースのレースコード。
            reference_races: 騎手ごとに参照する過去レース数。

        Returns:
            騎手別の戦術傾向。
        """

        rows = await fetch_rows(
            database,
            _SQL,
            {"race_code": race_code, "reference_races": max(1, min(reference_races, 500)), "limit": 100},
        )
        return RacePaceJockeyTacticsOutput(tactics=rows)

    return race_pace_jockey_tactics
