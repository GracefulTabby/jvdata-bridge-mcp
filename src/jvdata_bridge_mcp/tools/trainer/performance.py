"""調教師の基本成績を直接SQLで集計するMCPツール。"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._people import person_history_sql, person_period
from jvdata_bridge_mcp.tools._race_boundary import RaceBoundary, RaceCode, period_dates, resolve_race_boundary
from jvdata_bridge_mcp.tools._runtime import fetch_rows

TrainerCode = Annotated[str, Field(description="調教師コード")]


class TrainerPerformanceItem(BaseModel):
    """調教師のプロフィールと成績集計1件。"""

    model_config = ConfigDict(extra="forbid")

    trainer_code: str = Field(description="調教師コード")
    trainer_name: str | None = Field(default=None, description="調教師名")
    is_active: bool | None = Field(default=None, description="現役か")
    affiliation_name: str | None = Field(default=None, description="所属")
    licensed_date: str | None = Field(default=None, description="免許日")
    provisional_starts: int = 0
    starts: int = Field(description="出走数")
    wins: int = Field(description="勝利数")
    top3_finishes: int = Field(description="3着以内数")
    flat_prize_money_yen: int | None = Field(default=None, description="平地賞金。円")


class AnalyzeTrainerPerformanceOutput(BaseModel):
    """調教師のプロフィールと成績集計。"""

    aggregation_scope: str = "people_statistics:single_period:flat_total"
    period: str = "career"
    boundary: RaceBoundary | None = None
    performances: list[TrainerPerformanceItem] = Field(description="調教師プロフィールと成績")


_SQL = """
SELECT
    t.trainer_id AS trainer_code,
    t.trainer_name,
    t.is_active,
    t.affiliation_name,
    t.licensed_date,
    COALESCE(SUM(s.finish_count), 0) AS starts,
    COALESCE(SUM(s.finish_count) FILTER (WHERE s.finish_index = 0), 0) AS wins,
    COALESCE(SUM(s.finish_count) FILTER (WHERE s.finish_index <= 2), 0) AS top3_finishes,
    MAX(s.flat_prize_money_yen) AS flat_prize_money_yen
FROM jv_mart.trainers AS t
LEFT JOIN jv_mart.people_statistics AS s ON s.person_type = 'trainer' AND s.person_id = t.trainer_id
    AND s.period_index = :period_index AND s.condition_code = 'flat_total'
WHERE t.trainer_id = :trainer_code
GROUP BY t.trainer_id, t.trainer_name, t.is_active, t.affiliation_name, t.licensed_date
"""


def build_analyze_trainer_performance_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したanalyze_trainer_performance関数を作る。"""

    async def analyze_trainer_performance(
        trainer_code: TrainerCode,
        period: str = "career",
        race_code: RaceCode = "",
    ) -> AnalyzeTrainerPerformanceOutput:
        """調教師の基本成績を取得する。

        プロフィール、出走数、勝利数、複勝圏数、賞金を返す。

        Args:
            trainer_code: 調教師コード。

        Returns:
            調教師成績。
        """

        period_index = person_period(period)
        boundary = await resolve_race_boundary(database, race_code)
        if race_code:
            start, end = period_dates(date.fromisoformat(boundary.race_date), period)
            rows = await fetch_rows(
                database,
                "WITH stats AS ("
                + person_history_sql("trainer")
                + """
                ) SELECT s.*,t.is_active,t.affiliation_name,t.licensed_date
                FROM stats s LEFT JOIN jv_mart.trainers t ON t.trainer_id=s.trainer_code
                """,
                {
                    "person_id": trainer_code,
                    **boundary.parameters(),
                    "include_provisional": True,
                    "start_date": start,
                    "end_date": end,
                    "venue": "",
                    "surface": "flat",
                    "distance_min": 0,
                    "distance_max": 0,
                    "is_graded": False,
                },
                disable_jit=True,
            )
            allowed = set(TrainerPerformanceItem.model_fields)
            rows = [{k: v for k, v in row.items() if k in allowed} for row in rows]
            scope = "complete_stored_JRA_flat_runs_before_target_and_now; provisional_can_be_revised"
        else:
            rows = await fetch_rows(database, _SQL, {"trainer_code": trainer_code, "period_index": period_index})
            scope = "people_statistics:single_period:flat_total"
        return AnalyzeTrainerPerformanceOutput(
            performances=rows, period=period, aggregation_scope=scope, boundary=boundary
        )

    return analyze_trainer_performance
