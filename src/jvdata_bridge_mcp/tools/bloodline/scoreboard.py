"""出走馬の血統スコアボードを直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._runtime import fetch_rows

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]
Limit = Annotated[int, Field(description="返却する最大件数")]


class BloodlineScoreboardItem(BaseModel):
    """対象レースの出走馬別血統指標1件。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    horse_number: int | None = Field(default=None, description="馬番")
    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    pedigree_ancestor_count: int = Field(description="血統祖先件数")
    first_generation_count: int = Field(description="1世代目祖先数")
    distinct_ancestor_count: int = Field(description="異なる祖先数")
    sire_name: str | None = Field(default=None, description="父馬名")
    dam_name: str | None = Field(default=None, description="母馬名")


class BloodlineScoreboardOutput(BaseModel):
    """対象レースの出走馬別血統指標。"""

    scoreboard: list[BloodlineScoreboardItem] = Field(description="出走馬別血統指標")


_SQL = """
SELECT
    c.race_id AS race_code,
    c.horse_number,
    c.horse_id,
    c.horse_name,
    COUNT(p.ancestor_name) AS pedigree_ancestor_count,
    COUNT(*) FILTER (WHERE p.generation = 1) AS first_generation_count,
    COUNT(DISTINCT p.ancestor_name) AS distinct_ancestor_count,
    MAX(p.ancestor_name) FILTER (WHERE p.generation = 1 AND p.relation_code = 'sire') AS sire_name,
    MAX(p.ancestor_name) FILTER (WHERE p.generation = 1 AND p.relation_code = 'dam') AS dam_name
FROM jv_mart.race_cards AS c
LEFT JOIN jv_mart.horse_pedigree AS p ON p.horse_id = c.horse_id
WHERE c.race_id = :race_code

GROUP BY c.race_id, c.horse_number, c.horse_id, c.horse_name
ORDER BY pedigree_ancestor_count DESC, c.horse_number
LIMIT :limit
"""


def build_bloodline_scoreboard_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したbloodline_scoreboard関数を作る。"""

    async def bloodline_scoreboard(race_code: RaceCode, limit: Limit = 100) -> BloodlineScoreboardOutput:
        """対象レースの出走馬を血統指標で一覧する。

        血統祖先数、第1世代祖先数、重複を除いた祖先数、父母の名称を返す。

        Args:
            race_code: 対象レースのレースコード。
            limit: 返却する最大件数。

        Returns:
            出走馬別の血統指標。
        """

        rows = await fetch_rows(database, _SQL, {"race_code": race_code, "limit": max(1, min(limit, 1_000))})
        return BloodlineScoreboardOutput(scoreboard=rows)

    return bloodline_scoreboard
