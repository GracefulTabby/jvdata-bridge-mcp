"""競走馬成績集計を直接SQLで実行するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import RaceBoundary, RaceCode, before_boundary_sql, resolve_race_boundary
from jvdata_bridge_mcp.tools._result_policy import ResultScope, include_provisional
from jvdata_bridge_mcp.tools._runtime import fetch_rows

HorseName = Annotated[str, Field(description="競走馬名による検索条件")]
KettoTorokuBango = Annotated[str, Field(description="競走馬の血統登録番号")]


class HorsePerformanceItem(BaseModel):
    """全頭揃ったJRA結果に基づく競走馬の成績集計1件。"""

    model_config = ConfigDict(extra="forbid")

    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    starts: int = Field(description="全頭揃ったJRA結果の出走数")
    wins: int = Field(description="全頭揃ったJRA結果の勝利数")
    top3_finishes: int = Field(description="全頭揃ったJRA結果の3着以内数")
    average_finish_position: float | None = Field(default=None, description="全頭揃ったJRA結果の平均着順")
    average_win_odds: float | None = Field(default=None, description="全頭揃ったJRA結果の平均単勝オッズ")
    average_popularity: float | None = Field(default=None, description="全頭揃ったJRA結果の平均人気")
    average_last_three_furlongs: float | None = Field(
        default=None, description="全頭揃ったJRA結果の平均上がり3ハロン。秒"
    )
    first_race_date: str | None = Field(default=None, description="全頭揃ったJRA結果に含まれる初出走日")
    provisional_starts: int = 0
    latest_race_date: str | None = Field(default=None, description="全頭揃ったJRA結果に含まれる最終出走日")


class AnalyzeHorsePerformanceOutput(BaseModel):
    """全頭揃ったJRA結果による競走馬の成績集計。"""

    boundary: RaceBoundary | None = None
    result_policy: str = "complete"
    performances: list[HorsePerformanceItem] = Field(description="全頭揃ったJRA結果による競走馬の成績集計一覧")


_SQL = f"""
SELECT
    h.horse_id,
    MAX(h.horse_name) AS horse_name,
    COUNT(*) AS starts,
    COUNT(*) FILTER (WHERE h.is_final_result IS FALSE) AS provisional_starts,
    COUNT(*) FILTER (WHERE h.finish_position = 1) AS wins,
    COUNT(*) FILTER (WHERE h.finish_position <= 3) AS top3_finishes,
    ROUND(AVG(h.finish_position)::numeric, 3) AS average_finish_position,
    ROUND(AVG(h.final_win_odds)::numeric, 3) AS average_win_odds,
    ROUND(AVG(h.final_win_popularity)::numeric, 3) AS average_popularity,
    ROUND(AVG(h.last_three_furlongs_seconds)::numeric, 3) AS average_last_three_furlongs,
    MIN(h.race_date) AS first_race_date,
    MAX(h.race_date) AS latest_race_date
FROM jv_mart.horse_runs AS h
WHERE (h.is_final_result IS TRUE OR (:include_provisional AND h.result_status_code='provisional_complete'))
  AND h.is_complete_result IS TRUE AND h.is_starter IS TRUE
  AND {before_boundary_sql("h")}
  AND (:horse_name = '' OR h.horse_name ILIKE '%' || :horse_name || '%')
  AND (:ketto_toroku_bango = '' OR h.horse_id = :ketto_toroku_bango)
GROUP BY h.horse_id
ORDER BY starts DESC, wins DESC, h.horse_id
LIMIT :limit
"""


def build_analyze_horse_performance_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したanalyze_horse_performance関数を作る。"""

    async def analyze_horse_performance(
        horse_name: HorseName = "",
        ketto_toroku_bango: KettoTorokuBango = "",
        race_code: RaceCode = "",
        result_scope: ResultScope = "complete",
    ) -> AnalyzeHorsePerformanceOutput:
        """競走馬の成績を集計・分析する。

        JRAの全頭揃った成績・速報から出走数、勝利数、複勝圏数、平均着順、平均人気、末脚時計を集計する。

        Args:
            horse_name: 競走馬名の部分一致条件。
            ketto_toroku_bango: 血統登録番号。データモデル上のhorse_idも照合する。

        race_code指定時は対象レース以前の完了実績を自動集計する。当日の先行レースも含む。

        Returns:
            対象馬ごとのJRA確定成績集計。
        """

        boundary = await resolve_race_boundary(database, race_code)
        rows = await fetch_rows(
            database,
            _SQL,
            {
                "horse_name": horse_name,
                "ketto_toroku_bango": ketto_toroku_bango,
                "limit": 100,
                **boundary.parameters(),
                "include_provisional": include_provisional(result_scope),
            },
            disable_jit=True,
        )
        return AnalyzeHorsePerformanceOutput(performances=rows, boundary=boundary, result_policy=result_scope)

    return analyze_horse_performance
