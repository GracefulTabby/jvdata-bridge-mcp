"""騎手の直近成績を直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.pace._normalization import normalized_running_style_sql

JockeyCode = Annotated[str, Field(description="騎手コード")]
RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class JockeyRecentFormItem(BaseModel):
    """対象騎手の直近成績1件。"""

    model_config = ConfigDict(extra="forbid")

    jockey_code: str = Field(description="騎手コード")
    jockey_name: str | None = Field(default=None, description="騎手名")
    race_id: str = Field(description="レースコード")
    race_date: str | None = Field(default=None, description="開催日")
    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    race_name: str | None = Field(default=None, description="レース名")
    horse_id: str | None = Field(default=None, description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    finish_position: int | None = Field(default=None, description="着順")
    final_win_odds: float | None = Field(default=None, description="最終単勝オッズ")
    final_win_popularity: int | None = Field(default=None, description="最終単勝人気")
    is_final_result: bool | None = None
    result_status_code: str | None = None
    is_finisher: bool | None = None
    abnormality_code: str | None = None
    running_style_code: str | None = Field(default=None, description="データソースの脚質コード")
    running_style: str = Field(description="正規化脚質")


class JockeyRecentFormOutput(BaseModel):
    """対象騎手の直近成績。"""

    actual_count: int = 0
    scope: str = "complete_starters_before_target_and_now; provisional_can_be_revised; may_include_dnf"
    form: list[JockeyRecentFormItem] = Field(description="騎手の直近成績")


_NORMALIZED_RUNNING_STYLE = normalized_running_style_sql("result.running_style_code")


_SQL = f"""
WITH target AS MATERIALIZED (
    SELECT race_date
    FROM jv_data.races
    WHERE race_id = :race_code
), candidates AS MATERIALIZED (
    SELECT DISTINCT ON (e.race_id, e.horse_id)
        e.jockey_id,
        e.jockey_name,
        e.race_id,
        e.race_date,
        e.venue_code,
        e.horse_id,
        e.horse_name
    FROM jv_mart.entry_performances AS e
    JOIN jv_mart.race_results eligible ON eligible.race_id = e.race_id AND eligible.horse_id = e.horse_id
      AND (eligible.is_final_result IS TRUE OR eligible.result_status_code='provisional_complete') AND eligible.is_complete_result IS TRUE AND eligible.is_starter IS TRUE
    CROSS JOIN target AS t
    WHERE e.jockey_id = :jockey_code
      AND {before_target_race_sql("e", "t.race_date")}
      AND e.venue_code BETWEEN '01' AND '10'
    ORDER BY e.race_id, e.horse_id, e.source_sequence DESC NULLS LAST
), recent AS MATERIALIZED (
    SELECT * FROM candidates
    ORDER BY race_date DESC, race_id DESC, horse_id
    LIMIT :limit
)
SELECT
    e.jockey_id AS jockey_code,
    e.jockey_name,
    e.race_id,
    e.race_date,
    e.venue_code AS racecourse_code,
    race.race_name,
    e.horse_id,
    e.horse_name,
    result.finish_position, result.is_finisher, result.abnormality_code, result.is_final_result, result.result_status_code,
    result.final_win_odds,
    result.final_win_popularity,
    result.running_style_code,
    {_NORMALIZED_RUNNING_STYLE} AS running_style
FROM recent AS e
LEFT JOIN LATERAL (
    SELECT r.race_name
    FROM jv_data.races AS r
    WHERE r.race_id = e.race_id
    LIMIT 1
) AS race ON TRUE
LEFT JOIN LATERAL (
    SELECT
        rr.finish_position, rr.is_finisher, rr.abnormality_code, rr.is_final_result, rr.result_status_code,
        rr.final_win_odds,
        rr.final_win_popularity,
        rr.running_style_code
    FROM jv_mart.race_results AS rr
    WHERE rr.race_id = e.race_id
      AND rr.horse_id = e.horse_id
      AND (rr.is_final_result IS TRUE OR rr.result_status_code='provisional_complete') AND rr.is_complete_result IS TRUE AND rr.is_starter IS TRUE
    ORDER BY rr.source_record_id
    LIMIT 1
) AS result ON TRUE
ORDER BY e.race_date DESC, e.race_id DESC
"""


def build_jockey_recent_form_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したjockey_recent_form関数を作る。"""

    async def jockey_recent_form(jockey_code: JockeyCode, race_code: RaceCode) -> JockeyRecentFormOutput:
        """対象騎手の直近成績を取得する。

        対象レースより前の出走実績を新しい順に取得し、着順、人気、脚質を返す。

        Args:
            jockey_code: 騎手コード。
            race_code: 基準レースのレースコード。

        Returns:
            騎手の直近成績。
        """

        rows = await fetch_rows(database, _SQL, {"jockey_code": jockey_code, "race_code": race_code, "limit": 30})
        return JockeyRecentFormOutput(form=rows, actual_count=len(rows))

    return jockey_recent_form
