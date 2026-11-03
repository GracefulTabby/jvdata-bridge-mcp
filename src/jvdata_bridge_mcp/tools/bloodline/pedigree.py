"""出走馬の血統表を直接SQLで取得するMCPツール。"""

from __future__ import annotations

import re
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._runtime import fetch_rows

HorseNumber = Annotated[str, Field(description="出走表上の馬番")]
RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]
_HORSE_NUMBER_PATTERN = re.compile(r"(?:0?[1-9]|1[0-8])\Z")


def _normalize_horse_number(value: str) -> int:
    """馬番をDB照会用の整数へ正規化する。"""

    if not isinstance(value, str) or _HORSE_NUMBER_PATTERN.fullmatch(value) is None:
        raise ValueError("horse_number must be an ASCII number from 1 to 18")
    return int(value)


class PedigreeItem(BaseModel):
    """指定馬番の世代別血統表の1件。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    horse_number: int | None = Field(default=None, description="馬番")
    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    pedigree_position: int | None = Field(default=None, description="血統表上の位置")
    relation_code: str | None = Field(default=None, description="関係コード")
    relation_name: str | None = Field(default=None, description="関係名")
    generation: int | None = Field(default=None, description="世代")
    parent_side: str | None = Field(default=None, description="父系・母系")
    ancestor_breeding_id: str | None = Field(default=None, description="祖先血統ID")
    ancestor_name: str | None = Field(default=None, description="祖先名")


class BloodlineFetchPedigreeOutput(BaseModel):
    """指定馬番の世代別血統表。"""

    pedigrees: list[PedigreeItem] = Field(description="世代別血統表")


_SQL = """
SELECT
    c.race_id AS race_code,
    c.horse_number,
    c.horse_id,
    c.horse_name,
    p.pedigree_position,
    p.relation_code,
    p.relation_name,
    p.generation,
    p.parent_side,
    p.ancestor_breeding_id,
    p.ancestor_name
FROM jv_mart.race_cards AS c
LEFT JOIN jv_mart.horse_pedigree AS p ON p.horse_id = c.horse_id
WHERE c.race_id = :race_code AND c.horse_number = :horse_number
ORDER BY p.generation NULLS LAST, p.pedigree_position NULLS LAST
LIMIT :limit
"""


def build_bloodline_fetch_pedigree_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したbloodline_fetch_pedigree関数を作る。"""

    async def bloodline_fetch_pedigree(horse_number: HorseNumber, race_code: RaceCode) -> BloodlineFetchPedigreeOutput:
        """対象レースの指定馬番の血統表を取得する。

        馬番からhorse_idを解決し、世代、親側、祖先名を返す。

        Args:
            horse_number: 対象レースの馬番。
            race_code: 対象レースのレースコード。

        Returns:
            指定馬の血統表。
        """

        normalized_horse_number = _normalize_horse_number(horse_number)
        rows = await fetch_rows(
            database,
            _SQL,
            {"horse_number": normalized_horse_number, "race_code": race_code, "limit": 1_000},
        )
        return BloodlineFetchPedigreeOutput(pedigrees=rows)

    return bloodline_fetch_pedigree
