"""競走馬の基本情報を直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._runtime import fetch_rows

HorseName = Annotated[str, Field(description="競走馬名による検索条件")]
KettoTorokuBango = Annotated[str, Field(description="競走馬の血統登録番号")]


class HorseDetailsItem(BaseModel):
    """競走馬の基本情報1件。"""

    model_config = ConfigDict(extra="forbid")

    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    horse_name_kana: str | None = Field(default=None, description="馬名かな")
    horse_name_english: str | None = Field(default=None, description="英字馬名")
    sex_code: str | None = Field(default=None, description="性別コード")
    sex_name: str | None = Field(default=None, description="性別")
    breed_code: str | None = Field(default=None, description="品種コード")
    breed_name: str | None = Field(default=None, description="品種名")
    coat_color_code: str | None = Field(default=None, description="毛色コード")
    coat_color_name: str | None = Field(default=None, description="毛色")
    birth_date: str | None = Field(default=None, description="生年月日")
    registered_date: str | None = Field(default=None, description="登録日")
    deregistered_date: str | None = Field(default=None, description="抹消日")
    is_active: bool | None = Field(default=None, description="現役か")
    registration_status: str | None = Field(default=None, description="登録状態")
    trainer_id: str | None = Field(default=None, description="調教師ID")
    trainer_name: str | None = Field(default=None, description="調教師名")
    breeder_id: str | None = Field(default=None, description="生産者ID")
    breeder_name: str | None = Field(default=None, description="生産者名")
    owner_id: str | None = Field(default=None, description="馬主ID")
    owner_name: str | None = Field(default=None, description="馬主名")
    flat_prize_money_yen: int | None = Field(default=None, description="平地賞金。円")
    jump_prize_money_yen: int | None = Field(default=None, description="障害賞金。円")
    front_runner_count: int | None = Field(default=None, description="逃げ件数")
    stalker_count: int | None = Field(default=None, description="先行件数")
    closer_count: int | None = Field(default=None, description="差し件数")
    deep_closer_count: int | None = Field(default=None, description="追込件数")
    registered_race_count: int | None = Field(default=None, description="登録レース数")


class GetHorseDetailsOutput(BaseModel):
    """競走馬の基本情報。"""

    horses: list[HorseDetailsItem] = Field(description="競走馬詳細一覧")


_SQL = """
SELECT
    h.horse_id,
    h.horse_name,
    h.horse_name_kana,
    h.horse_name_english,
    h.sex_code,
    h.sex_name,
    h.breed_code,
    h.breed_name,
    h.coat_color_code,
    h.coat_color_name,
    h.birth_date,
    h.registered_date,
    h.deregistered_date,
    h.is_active,
    h.registration_status,
    h.trainer_id,
    h.trainer_short_name AS trainer_name,
    h.breeder_id,
    h.breeder_name,
    h.owner_id,
    h.owner_name,
    h.flat_prize_money_yen,
    h.jump_prize_money_yen,
    h.front_runner_count,
    h.stalker_count,
    h.closer_count,
    h.deep_closer_count,
    h.registered_race_count
FROM jv_mart.horses AS h
WHERE (:horse_name = '' OR h.horse_name ILIKE '%' || :horse_name || '%')
  AND (:ketto_toroku_bango = '' OR h.horse_id = :ketto_toroku_bango)
ORDER BY h.is_active DESC, h.horse_id
LIMIT :limit
"""


def build_get_horse_details_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したget_horse_details関数を作る。"""

    async def get_horse_details(
        horse_name: HorseName = "",
        ketto_toroku_bango: KettoTorokuBango = "",
    ) -> GetHorseDetailsOutput:
        """競走馬の基本情報を取得する。

        競走馬名または血統登録番号で検索し、プロフィール、所属、賞金、脚質集計を返す。

        Args:
            horse_name: 競走馬名の部分一致条件。
            ketto_toroku_bango: 血統登録番号。データモデル上のhorse_idも照合する。

        Returns:
            競走馬の基本情報。
        """

        normalized_horse_name = horse_name.strip()
        normalized_ketto_toroku_bango = ketto_toroku_bango.strip()
        if not normalized_horse_name and not normalized_ketto_toroku_bango:
            raise ValueError("horse_name or ketto_toroku_bango is required")
        rows = await fetch_rows(
            database,
            _SQL,
            {
                "horse_name": normalized_horse_name,
                "ketto_toroku_bango": normalized_ketto_toroku_bango,
                "limit": 100,
            },
        )
        return GetHorseDetailsOutput(horses=rows)

    return get_horse_details
