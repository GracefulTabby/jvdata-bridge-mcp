"""複数競走馬の比較を直接SQLで実行するMCPツール。"""

from __future__ import annotations

import re
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._runtime import fetch_rows

HorseToken = Annotated[
    str,
    Field(description="比較する競走馬名または馬IDをカンマ、読点、空白で指定"),
]


class HorseComparisonItem(BaseModel):
    """比較対象競走馬のプロフィールと賞金。"""

    model_config = ConfigDict(extra="forbid")

    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    sex_name: str | None = Field(default=None, description="性別")
    birth_date: str | None = Field(default=None, description="生年月日")
    is_active: bool | None = Field(default=None, description="現役か")
    trainer_name: str | None = Field(default=None, description="調教師名")
    flat_prize_money_yen: int | None = Field(default=None, description="平地賞金。円")
    registered_race_count: int | None = Field(default=None, description="登録レース数")
    front_runner_count: int | None = Field(default=None, description="逃げ件数")
    stalker_count: int | None = Field(default=None, description="先行件数")
    closer_count: int | None = Field(default=None, description="差し件数")
    deep_closer_count: int | None = Field(default=None, description="追込件数")


class CompareHorsesOutput(BaseModel):
    """比較対象競走馬のプロフィールと賞金。"""

    horses: list[HorseComparisonItem] = Field(description="比較対象の競走馬一覧")


_SQL = """
WITH requested AS (
    SELECT ordinality AS request_order, trim(value) AS token
    FROM regexp_split_to_table(:horse_names, '[,、[:space:]]+') WITH ORDINALITY AS parts(value, ordinality)
    WHERE trim(value) <> ''
)
SELECT
    h.horse_id,
    h.horse_name,
    h.sex_name,
    h.birth_date,
    h.is_active,
    h.trainer_short_name AS trainer_name,
    h.flat_prize_money_yen,
    h.registered_race_count,
    h.front_runner_count,
    h.stalker_count,
    h.closer_count,
    h.deep_closer_count
FROM requested AS q
JOIN jv_mart.horses AS h
  ON h.horse_id = q.token
  OR (
      h.horse_name = q.token
      AND NOT EXISTS (
          SELECT 1
          FROM jv_mart.horses AS id_match
          WHERE id_match.horse_id = q.token
      )
  )
ORDER BY q.request_order, h.horse_id
LIMIT :limit
"""


def build_compare_horses_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したcompare_horses関数を作る。"""

    async def compare_horses(horse_names: HorseToken) -> CompareHorsesOutput:
        """複数の競走馬を比較する。

        馬名または馬IDをカンマ、読点、空白で区切り、プロフィール、賞金、出走数、
        脚質集計を比較する。各tokenは、馬IDとして完全一致する場合はIDを優先し、
        それ以外は馬名の完全一致で検索する。同名馬を区別する場合は馬IDを指定する。

        Args:
            horse_names: 比較する競走馬名または馬IDを区切り文字で指定する。

        Returns:
            指定された競走馬の比較行。
        """

        normalized_horse_names = horse_names.strip()
        if not any(token for token in re.split(r"[,、\s]+", normalized_horse_names) if token):
            raise ValueError("horse_names must contain at least one horse ID or exact name")
        rows = await fetch_rows(database, _SQL, {"horse_names": normalized_horse_names, "limit": 100})
        return CompareHorsesOutput(horses=rows)

    return compare_horses
