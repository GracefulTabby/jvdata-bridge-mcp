"""直前21日の調教本数と有効4F時計から強度を計算するMCPツール。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import training_before_target_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.training._normalization import (
    display_horse_number,
    horse_number_sort_key,
    is_plausible_four_furlongs_seconds,
    is_valid_training_quality,
    valid_training_quality_sql,
)

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]

INTENSITY_SEMANTICS = (
    "直前21日の品質有効セッション本数を最大40点、妥当な4F時計の強度（52秒以下=強、"
    "54秒以下=標準）を加点し、0〜100に上限を設けた調教強度score"
)
_VALID_QUALITY = valid_training_quality_sql("s")


class TrainingIntensityItem(BaseModel):
    """出走馬1頭の直前調教強度。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: str = Field(description="出走表上の馬番")
    horse_name: str = Field(description="馬名")
    intensity_score: float | None = Field(default=None, description="調教強度score。時計不足時はnull")
    intensity_label: str | None = Field(default=None, description="調教強度ラベル")
    session_count: int = Field(description="直前21日の有効品質セッション数")


class TrainingIntensityAnalysisOutput(BaseModel):
    """出走馬別の直前調教強度。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    lookback_days: int = Field(description="参照したレース前日数")
    items: list[TrainingIntensityItem] = Field(description="出走馬別の調教強度")
    count: int = Field(description="対象出走馬数")
    score_semantics: str = Field(description="intensity scoreの意味")


_SQL = f"""
WITH target AS MATERIALIZED (
    SELECT race_date
    FROM jv_data.races
    WHERE race_id = :race_code
), target_cards AS MATERIALIZED (
    SELECT DISTINCT ON (c.race_id, c.horse_number)
        c.race_id,
        c.horse_number,
        c.horse_id,
        c.horse_name
    FROM jv_mart.race_cards AS c
    WHERE c.race_id = :race_code
      AND c.horse_number IS NOT NULL
      AND c.horse_id IS NOT NULL


    ORDER BY c.race_id, c.horse_number, c.horse_id
)
SELECT
    c.horse_number,
    c.horse_id,
    c.horse_name,
    s.training_date,
    s.training_time,
    s.training_type,
    s.quality_status,
    s.four_furlongs_seconds
FROM target_cards AS c
CROSS JOIN target AS r
LEFT JOIN LATERAL (
    SELECT
        s.training_date,
        s.training_time,
        s.training_type,
        s.quality_status,
        s.four_furlongs_seconds
    FROM jv_mart.training_sessions AS s
    WHERE s.horse_id = c.horse_id
      AND s.training_date >= r.race_date - INTERVAL '21 days'
      AND {training_before_target_sql("s", "r.race_date")}
      AND {_VALID_QUALITY}
    ORDER BY s.training_date DESC, s.training_time DESC NULLS LAST, s.source_sequence DESC NULLS LAST
    LIMIT 100
) AS s ON TRUE
ORDER BY c.horse_number, s.training_date DESC NULLS LAST, s.training_time DESC NULLS LAST
LIMIT :limit
"""


def _score(rows: list[Mapping[str, object]]) -> tuple[float | None, str | None]:
    """本数と妥当な4F時計の強度からscore/labelを返す。"""

    sessions = [
        row
        for row in rows
        if row.get("training_date") is not None and is_valid_training_quality(row.get("quality_status"))
    ]
    if not sessions:
        return None, None
    strong = 0
    normal = 0
    for session in sessions:
        value = session.get("four_furlongs_seconds")
        four_furlongs = float(value) if isinstance(value, (int, float)) else None
        if not is_plausible_four_furlongs_seconds(four_furlongs):
            continue
        assert four_furlongs is not None
        if four_furlongs <= 52.0:
            strong += 1
        elif four_furlongs <= 54.0:
            normal += 1
    base_score = min(40.0, len(sessions) * 5.0)
    score = min(100.0, base_score + (strong * 4.0 + normal * 2.0) * 5.0)
    label = "高" if score >= 70.0 else "中" if score >= 40.0 else "低"
    return score, label


def build_training_intensity_analysis_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したtraining_intensity_analysis関数を作る。"""

    async def training_intensity_analysis(race_code: RaceCode) -> TrainingIntensityAnalysisOutput:
        """対象レースの直前21日について本数と有効4F時計の強度を返す。"""

        rows = await fetch_rows(database, _SQL, {"race_code": race_code, "limit": 2_000})
        grouped: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
        cards: dict[str, Mapping[str, object]] = {}
        for row in rows:
            horse_id = str(row.get("horse_id") or "")
            if not horse_id:
                continue
            cards.setdefault(horse_id, row)
            if row.get("training_date") is not None:
                grouped[horse_id].append(row)
        items: list[TrainingIntensityItem] = []
        for row in sorted(cards.values(), key=lambda value: horse_number_sort_key(value.get("horse_number"))):
            horse_id = str(row.get("horse_id") or "")
            sessions = grouped[horse_id]
            score, label = _score(sessions)
            items.append(
                TrainingIntensityItem(
                    horse_number=display_horse_number(row.get("horse_number")),
                    horse_name=str(row.get("horse_name") or ""),
                    intensity_score=score,
                    intensity_label=label,
                    session_count=len(sessions),
                )
            )
        items.sort(
            key=lambda item: (
                item.intensity_score is None,
                -(item.intensity_score or 0.0),
                horse_number_sort_key(item.horse_number),
            )
        )
        return TrainingIntensityAnalysisOutput(
            race_code=race_code,
            lookback_days=21,
            items=items,
            count=len(items),
            score_semantics=INTENSITY_SEMANTICS,
        )

    return training_intensity_analysis
