"""対象馬の血統と近走フォームを直接SQLで取得するMCPツール。"""

from __future__ import annotations

import re
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows

HorseNumber = Annotated[str, Field(description="出走表上の馬番")]
RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]
_HORSE_NUMBER_PATTERN = re.compile(r"(?:0?[1-9]|1[0-8])\Z")


def _normalize_horse_number(value: str) -> int:
    """馬番をDB照会用の整数へ正規化する。"""

    if not isinstance(value, str) or _HORSE_NUMBER_PATTERN.fullmatch(value) is None:
        raise ValueError("horse_number must be an ASCII number from 1 to 18")
    return int(value)


class BloodlineFormProfileItem(BaseModel):
    """対象馬の血統祖先と近走フォーム1件。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: int | None = Field(default=None, description="馬番")
    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    ancestor_name: str | None = Field(default=None, description="祖先名")
    generation: int | None = Field(default=None, description="世代")
    parent_side: str | None = Field(default=None, description="父系・母系")
    recent_race_code: str | None = Field(default=None, description="直近レースコード")
    race_date: str | None = Field(default=None, description="開催日")
    is_final_result: bool | None = None
    result_status_code: str | None = None
    finish_position: int | None = Field(default=None, description="着順")
    distance_meters: int | None = Field(default=None, description="距離。m")
    track_name: str | None = Field(default=None, description="走路名")
    last_three_furlongs_seconds: float | None = Field(default=None, description="上がり3ハロン。秒")


class BloodlineFormProfileOutput(BaseModel):
    """対象馬の血統祖先と近走フォーム。"""

    profiles: list[BloodlineFormProfileItem] = Field(description="血統と近走フォーム")


_SQL = f"""
WITH target AS (
    SELECT horse_id, horse_number, horse_name, race_date
    FROM jv_mart.race_cards
    WHERE race_id = :race_code AND horse_number = :horse_number
)
SELECT
    t.horse_number,
    t.horse_id,
    t.horse_name,
    p.ancestor_name,
    p.generation,
    p.parent_side,
    h.race_id AS recent_race_code,
    h.race_date,
    h.finish_position,h.is_final_result,h.result_status_code,
    h.distance_meters,
    h.track_name,
    h.last_three_furlongs_seconds
FROM target AS t
LEFT JOIN jv_mart.horse_pedigree AS p ON p.horse_id = t.horse_id AND p.generation <= 2
LEFT JOIN LATERAL (
    SELECT * FROM jv_mart.horse_runs AS hr
    WHERE hr.horse_id = t.horse_id
      AND {before_target_race_sql("hr", "t.race_date")}
      AND (hr.is_final_result IS TRUE OR hr.result_status_code='provisional_complete') AND hr.is_complete_result IS TRUE AND hr.is_starter IS TRUE
    ORDER BY hr.race_date DESC, hr.race_id DESC
    LIMIT :recent_runs
) AS h ON TRUE
ORDER BY p.generation NULLS LAST, h.race_date DESC
LIMIT :limit
"""


def build_bloodline_form_profile_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したbloodline_form_profile関数を作る。"""

    async def bloodline_form_profile(horse_number: HorseNumber, race_code: RaceCode) -> BloodlineFormProfileOutput:
        """対象馬の血統と近走フォームを組み合わせて分析する。

        第2世代までの祖先と、直近走の着順・距離・走路・末脚時計を返す。

        Args:
            horse_number: 対象レースの馬番。
            race_code: 対象レースのレースコード。

        Returns:
            血統と近走事実。
        """

        normalized_horse_number = _normalize_horse_number(horse_number)
        rows = await fetch_rows(
            database,
            _SQL,
            {
                "horse_number": normalized_horse_number,
                "race_code": race_code,
                "recent_runs": 5,
                "limit": 500,
            },
        )
        return BloodlineFormProfileOutput(profiles=rows)

    return bloodline_form_profile
