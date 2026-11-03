"""競走馬の過去走履歴を直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import RaceBoundary, RaceCode, before_boundary_sql, resolve_race_boundary
from jvdata_bridge_mcp.tools._result_policy import ResultScope, include_provisional
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.pace._normalization import normalized_running_style_sql

HorseName = Annotated[str, Field(description="競走馬名による検索条件")]
KettoTorokuBango = Annotated[str, Field(description="競走馬の血統登録番号")]
Limit = Annotated[int, Field(description="返却する最大件数")]


class HorseRaceHistoryItem(BaseModel):
    """競走馬の過去走履歴1件。"""

    model_config = ConfigDict(extra="forbid")

    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    race_id: str = Field(description="レースコード")
    race_date: str | None = Field(default=None, description="開催日")
    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    racecourse_name: str | None = Field(default=None, description="競馬場名")
    race_number: int | None = Field(default=None, description="レース番号")
    race_name: str | None = Field(default=None, description="レース名")
    grade_code: str | None = Field(default=None, description="グレードコード")
    grade_name: str | None = Field(default=None, description="グレード名")
    distance_meters: int | None = Field(default=None, description="距離。m")
    track_code: str | None = Field(default=None, description="走路コード")
    track_name: str | None = Field(default=None, description="走路名")
    horse_number: int | None = Field(default=None, description="馬番")
    finish_position: int | None = Field(default=None, description="着順")
    elapsed_seconds: float | None = Field(default=None, description="走破タイム。秒")
    time_difference_seconds: float | None = Field(default=None, description="着差。秒")
    final_win_odds: float | None = Field(default=None, description="最終単勝オッズ")
    final_win_popularity: int | None = Field(default=None, description="最終単勝人気")
    running_style_code: str | None = Field(default=None, description="データソースの脚質コード")
    running_style: str = Field(description="正規化脚質")
    result_status_code: str | None = None
    is_complete_result: bool | None = None
    is_final_result: bool | None = Field(default=None, description="確定結果か")


class AnalyzeHorseRaceHistoryOutput(BaseModel):
    """競走馬の過去走履歴。"""

    boundary: RaceBoundary | None = None
    result_policy: str = "complete"
    history: list[HorseRaceHistoryItem] = Field(description="競走馬のレース履歴")


_NORMALIZED_RUNNING_STYLE = normalized_running_style_sql("h.running_style_code")


_SQL = f"""
SELECT
    h.horse_id,
    h.horse_name,
    h.race_id,
    h.race_date,
    h.venue_code AS racecourse_code,
    h.venue_name AS racecourse_name,
    h.race_number,
    h.race_name,
    h.grade_code,
    h.grade_name,
    h.distance_meters,
    h.track_code,
    h.track_name,
    h.horse_number,
    h.finish_position,
    h.elapsed_seconds,
    h.time_difference_seconds,
    h.final_win_odds,
    h.final_win_popularity,
    h.running_style_code,
    {_NORMALIZED_RUNNING_STYLE} AS running_style,
    h.is_final_result,h.is_complete_result,h.result_status_code
FROM jv_mart.horse_runs AS h
WHERE h.is_complete_result IS TRUE AND h.is_starter IS TRUE
  AND (h.is_final_result IS TRUE OR (:include_provisional AND h.result_status_code='provisional_complete'))
  AND {before_boundary_sql("h")}
  AND (:horse_name = '' OR h.horse_name ILIKE '%' || :horse_name || '%')
  AND (:ketto_toroku_bango = '' OR h.horse_id = :ketto_toroku_bango)
ORDER BY h.race_date DESC, h.race_id DESC
LIMIT :limit
"""


def build_analyze_horse_race_history_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したanalyze_horse_race_history関数を作る。"""

    async def analyze_horse_race_history(
        horse_name: HorseName = "",
        ketto_toroku_bango: KettoTorokuBango = "",
        limit: Limit = 10,
        race_code: RaceCode = "",
        result_scope: ResultScope = "complete",
    ) -> AnalyzeHorseRaceHistoryOutput:
        """競走馬の過去走履歴を分析する。

        レース日、条件、着順、時計、人気、脚質を速報・確定の状態付きで新しい順で返す。

        Args:
            horse_name: 競走馬名の部分一致条件。
            ketto_toroku_bango: 血統登録番号。データモデル上のhorse_idも照合する。
            limit: 返却する最大件数。

        race_code指定時は未発走の対象レース以前の実績を自動選択する。当日の先行レースも含む。

        Returns:
            対象馬の過去走履歴。
        """

        boundary = await resolve_race_boundary(database, race_code)
        rows = await fetch_rows(
            database,
            _SQL,
            {
                "horse_name": horse_name,
                **boundary.parameters(),
                "include_provisional": include_provisional(result_scope),
                "ketto_toroku_bango": ketto_toroku_bango,
                "limit": max(1, min(limit, 1_000)),
            },
            disable_jit=True,
        )
        return AnalyzeHorseRaceHistoryOutput(history=rows, boundary=boundary, result_policy=result_scope)

    return analyze_horse_race_history
