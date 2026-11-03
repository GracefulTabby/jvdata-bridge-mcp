"""競走馬検索を直接SQLで実行するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._runtime import fetch_rows

HorseName = Annotated[str, Field(description="競走馬名による検索条件")]
Limit = Annotated[int, Field(description="返却する最大件数")]


class HorseSearchItem(BaseModel):
    """競走馬検索結果の1件。"""

    model_config = ConfigDict(extra="forbid")

    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    horse_name_kana: str | None = Field(default=None, description="馬名かな")
    sex_name: str | None = Field(default=None, description="性別")
    birth_date: str | None = Field(default=None, description="生年月日")
    is_active: bool | None = Field(default=None, description="現役か")
    trainer_id: str | None = Field(default=None, description="調教師ID")
    trainer_name: str | None = Field(default=None, description="調教師名")
    owner_name: str | None = Field(default=None, description="馬主名")
    breeder_name: str | None = Field(default=None, description="生産者名")
    flat_prize_money_yen: int | None = Field(default=None, description="平地賞金。円")
    registered_race_count: int | None = Field(default=None, description="登録レース数")


class SearchHorsesOutput(BaseModel):
    """競走馬名に一致する競走馬一覧。"""

    horses: list[HorseSearchItem] = Field(description="検索に一致した競走馬")


_SQL = """
SELECT
    h.horse_id,
    h.horse_name,
    h.horse_name_kana,
    h.sex_name,
    h.birth_date,
    h.is_active,
    h.trainer_id,
    h.trainer_short_name AS trainer_name,
    h.owner_name,
    h.breeder_name,
    h.flat_prize_money_yen,
    h.registered_race_count
FROM jv_mart.horses AS h
WHERE h.horse_name ILIKE '%' || :horse_name || '%'
ORDER BY h.is_active DESC, h.flat_prize_money_yen DESC NULLS LAST, h.horse_id
LIMIT :limit
"""


def build_search_horses_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したsearch_horses関数を作る。"""

    async def search_horses(horse_name: HorseName, limit: Limit = 20) -> SearchHorsesOutput:
        """競走馬名で競走馬を検索する。

        部分一致で検索し、現役馬、賞金、馬IDの順に最大件数を返す。

        Args:
            horse_name: 検索する競走馬名の一部。
            limit: 返却する最大件数。

        Returns:
            条件に一致した競走馬の基本情報。
        """

        rows = await fetch_rows(
            database,
            _SQL,
            {"horse_name": horse_name, "limit": max(1, min(limit, 1_000))},
        )
        return SearchHorsesOutput(horses=rows)

    return search_horses
