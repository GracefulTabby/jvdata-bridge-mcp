"""対象レースの保存済み出馬表から、予想に使う基本項目だけを返す。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_date import race_date_from_code
from jvdata_bridge_mcp.tools._runtime import fetch_rows

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class EntryItem(BaseModel):
    """市場と結果を含まない現在収録の基本出馬表。"""

    model_config = ConfigDict(extra="forbid")
    race_code: str
    horse_number: int
    horse_id: str
    horse_name: str
    frame_number: int | None = None
    sex_name: str | None = None
    horse_age: int | None = None
    jockey_id: str | None = None
    jockey_name: str | None = None
    trainer_id: str | None = None
    trainer_name: str | None = None
    assigned_weight_kg: float | None = None


class EntriesListOutput(BaseModel):
    """保存済みの基本項目を復元した馬番順の出馬表。"""

    entries: list[EntryItem] = Field(description="馬番順の復元出馬表")
    race_date: str
    market_fields_included: bool = False
    target_results_included: bool = False
    reconstruction_status: str = "current_stored_entry_fields; no_historical_replay"


_SQL = """
SELECT DISTINCT ON (c.race_id, c.horse_number)
    c.race_id AS race_code, c.horse_number, c.horse_id, c.horse_name, c.frame_number, c.sex_name, c.horse_age,
    c.jockey_id, c.jockey_name, c.trainer_id, c.trainer_name, c.assigned_weight_kg
FROM jv_mart.race_cards AS c
WHERE c.race_id = :race_code AND c.horse_number BETWEEN 1 AND 18
ORDER BY c.race_id, c.horse_number, c.horse_id
"""


def build_entries_list_tool(database: JvDataDatabase) -> Callable[..., object]:
    """read-only DBを注入したentries_list関数を作る。"""

    async def entries_list(race_code: RaceCode) -> EntriesListOutput:
        """保存済み出馬表の馬、枠、騎手、調教師、斤量を取得する。

        馬体重、結果側の取消・除外状態、対象レースのオッズ・人気・結果は含めない。
        現在のAV/JC通知はrace_forecast_context.current_entry_observationsで別に確認する。
        現在収録の基本項目を返す。過去時点の出馬表の再現は行わない。
        """

        target_date = race_date_from_code(race_code)
        rows = await fetch_rows(database, _SQL, {"race_code": race_code})
        return EntriesListOutput(entries=rows, race_date=target_date.isoformat())

    return entries_list
