"""騎手ランキングを直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._runtime import fetch_rows

Limit = Annotated[int, Field(description="返却する最大件数")]
SortBy = Annotated[str, Field(description="結果を並べる基準")]
MinStarts = Annotated[int, Field(description="ランキング対象にする最小出走数")]


class TopJockeyItem(BaseModel):
    """成績上位の騎手ランキング1件。"""

    model_config = ConfigDict(extra="forbid")

    jockey_code: str = Field(description="騎手コード")
    jockey_name: str | None = Field(default=None, description="騎手名")
    starts: int = Field(description="出走数")
    wins: int = Field(description="勝利数")
    top3_finishes: int = Field(description="3着以内数")
    win_rate: float | None = Field(default=None, description="勝率")
    flat_prize_money_yen: int | None = Field(default=None, description="平地賞金。円")


class GetTopJockeysOutput(BaseModel):
    """成績上位の騎手ランキング。"""

    rankings: list[TopJockeyItem] = Field(description="成績上位の騎手")


_SQL = """
WITH overall AS (
    SELECT
        s.person_id AS jockey_code,
        MAX(s.person_name) AS jockey_name,
        SUM(s.finish_count) AS starts,
        SUM(s.finish_count) FILTER (WHERE s.finish_index = 0) AS wins,
        SUM(s.finish_count) FILTER (WHERE s.finish_index <= 2) AS top3_finishes,
        MAX(s.flat_prize_money_yen) AS flat_prize_money_yen
    FROM jv_mart.people_statistics AS s
    WHERE s.person_type = 'jockey'
      AND s.period_index = 3
      AND s.condition_code = 'flat_total'
    GROUP BY s.person_id
), ranked AS (
    SELECT
        jockey_code,
        jockey_name,
        starts,
        wins,
        top3_finishes,
        ROUND(wins::numeric / NULLIF(starts, 0), 4) AS win_rate,
        flat_prize_money_yen
    FROM overall
    WHERE starts >= :min_starts
)
SELECT
    jockey_code,
    jockey_name,
    starts,
    wins,
    top3_finishes,
    win_rate,
    flat_prize_money_yen
FROM ranked
ORDER BY
    CASE WHEN :sort_by = 'prize' THEN flat_prize_money_yen END DESC NULLS LAST,
    CASE WHEN :sort_by <> 'prize' THEN win_rate END DESC NULLS LAST,
    wins DESC, starts DESC, jockey_code
LIMIT :limit
"""


def build_get_top_jockeys_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したget_top_jockeys関数を作る。"""

    async def get_top_jockeys(
        limit: Limit = 20,
        sort_by: SortBy = "win_rate",
        min_starts: MinStarts = 30,
    ) -> GetTopJockeysOutput:
        """騎手の成績上位ランキングを取得する。

        平地総合のoverall（累計）成績だけを対象に、勝率または賞金を基準に、
        出走数、勝利数、複勝圏数を返す。勝率順は少数出走の偶然を避けるため、
        ``min_starts``未満の騎手をランキングから除外する。返却する``win_rate``
        は縮小推定値ではなく、overallの勝数/出走数によるraw値である。

        Args:
            limit: 返却する最大件数。
            sort_by: win_rateまたはprize。
            min_starts: ランキング対象にする最小出走数。既定値は30。

        Returns:
            騎手ランキング。
        """

        rows = await fetch_rows(
            database,
            _SQL,
            {
                "limit": max(1, min(limit, 1_000)),
                "sort_by": sort_by,
                "min_starts": max(0, min(min_starts, 1_000_000)),
            },
        )
        return GetTopJockeysOutput(rankings=rows)

    return get_top_jockeys
