"""対象レースの調教師比較を直接SQLで実行するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class TrainerComparisonItem(BaseModel):
    """対象レース出走馬の調教師比較1件。"""

    model_config = ConfigDict(extra="forbid")

    trainer_code: str = Field(description="調教師コード")
    trainer_name: str | None = Field(default=None, description="調教師名")
    starts: int = Field(description="出走数")
    wins: int = Field(description="勝利数")
    top3_finishes: int = Field(description="3着以内数")
    earned_prize_money_yen: int | None = Field(default=None, description="獲得賞金。円")


class CompareRaceTrainersOutput(BaseModel):
    """対象レース出走馬の調教師比較。"""

    result_policy: str = "complete_official_or_provisional; provisional_can_be_revised"

    trainers: list[TrainerComparisonItem] = Field(description="調教師比較")


_SQL = f"""
WITH target AS MATERIALIZED (
    SELECT DISTINCT ON (trainer_id) trainer_id,trainer_name,race_date
    FROM jv_mart.race_cards WHERE race_id=:race_code AND trainer_id IS NOT NULL
    ORDER BY trainer_id,horse_number
)
SELECT t.trainer_id AS trainer_code,t.trainer_name,
       h.starts,h.wins,h.top3_finishes,h.earned_prize_money_yen
FROM target t CROSS JOIN LATERAL (
    SELECT COUNT(*) AS starts,COUNT(*) FILTER (WHERE rr.finish_position=1) AS wins,
           COUNT(*) FILTER (WHERE rr.finish_position BETWEEN 1 AND 3) AS top3_finishes,
           SUM(rr.prize_money_yen) AS earned_prize_money_yen
    FROM (
        SELECT DISTINCT ON (e.race_id,e.horse_id) e.race_id,e.horse_id
        FROM jv_mart.entry_performances e WHERE e.trainer_id=t.trainer_id AND {before_target_race_sql("e", "t.race_date")}
        ORDER BY e.race_id,e.horse_id,e.source_sequence DESC NULLS LAST
    ) e
    JOIN LATERAL (
        SELECT rr.finish_position,rr.prize_money_yen
        FROM jv_mart.race_results rr WHERE rr.race_id=e.race_id AND rr.horse_id=e.horse_id
          AND (rr.is_final_result IS TRUE OR rr.result_status_code='provisional_complete') AND rr.is_complete_result IS TRUE AND rr.is_starter IS TRUE
        ORDER BY rr.source_record_id LIMIT 1
    ) rr ON TRUE
) h
ORDER BY h.wins DESC,h.top3_finishes DESC,h.starts DESC,t.trainer_id LIMIT :limit
"""


def build_compare_race_trainers_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したcompare_race_trainers関数を作る。"""

    async def compare_race_trainers(race_code: RaceCode) -> CompareRaceTrainersOutput:
        """対象レースに出走する調教師を比較する。

        調教師ごとの過去出走、勝利、複勝圏、賞金を返す。

        Args:
            race_code: 対象レースのレースコード。

        Returns:
            調教師比較。
        """

        rows = await fetch_rows(database, _SQL, {"race_code": race_code, "limit": 100}, disable_jit=True)
        return CompareRaceTrainersOutput(trainers=rows)

    return compare_race_trainers
