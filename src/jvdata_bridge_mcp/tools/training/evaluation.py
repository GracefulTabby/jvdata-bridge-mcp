"""対象レースの直前調教を自己内相対で評価するMCPツール。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import training_before_target_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.training._normalization import (
    display_horse_number,
    display_training_center,
    valid_four_furlongs_sql,
    valid_training_quality_sql,
)

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]

EVALUATION_SEMANTICS = "relative_to_self_training_rank"
_VALID_FOUR_FURLONGS = valid_four_furlongs_sql("s.four_furlongs_seconds")
_VALID_QUALITY = valid_training_quality_sql("s")


class RaceTrainingEvaluationItem(BaseModel):
    """出走馬1頭の直前調教評価。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: str = Field(description="出走表上の馬番")
    horse_name: str = Field(description="馬名")
    ketto_toroku_bango: str = Field(description="競走馬登録番号")
    training_center: str | None = Field(default=None, description="最新の比較可能な調教拠点")
    rank: str | None = Field(default=None, description="自己内相対4F評価。A〜D、比較不能時はnull")
    comparable_session_count: int = 0
    missing_reason: str | None = None
    fitness_inference: str | None = None
    effort_and_pairing: str = "unknown"
    rank_reason: str = Field(description="評価ランクの根拠またはサンプル不足理由")
    score_semantics: str = Field(description="rankの意味")


class RaceTrainingEvaluationOutput(BaseModel):
    """出走馬ごとの直前調教評価。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    items: list[RaceTrainingEvaluationItem] = Field(description="出走馬別の調教評価")
    count: int = Field(description="評価対象の出走馬数")


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
), session_rows AS MATERIALIZED (
    SELECT
        c.horse_number,
        c.horse_id,
        c.horse_name,
        s.training_date,
        s.training_time,
        s.training_type,
        s.training_center_code, s.course_code,
        s.quality_status,
        s.four_furlongs_seconds,
        s.source_sequence,
        ROW_NUMBER() OVER (
            PARTITION BY c.horse_id
            ORDER BY s.training_date DESC, s.training_time DESC NULLS LAST, s.source_sequence DESC NULLS LAST
        ) AS session_number
    FROM target_cards AS c
    CROSS JOIN target AS r
    JOIN LATERAL (
        SELECT
            s.training_date,
            s.training_time,
            s.training_type,
            s.training_center_code, s.course_code,
            s.quality_status,
            s.four_furlongs_seconds,
            s.source_sequence
        FROM jv_mart.training_sessions AS s
        WHERE s.horse_id = c.horse_id
          AND s.training_date >= r.race_date - INTERVAL '21 days'
          AND {training_before_target_sql("s", "r.race_date")}
          AND {_VALID_QUALITY}
          AND {_VALID_FOUR_FURLONGS}
        ORDER BY s.training_date DESC, s.training_time DESC NULLS LAST, s.source_sequence DESC NULLS LAST
        LIMIT 100
    ) AS s ON TRUE
), latest_type AS MATERIALIZED (
    SELECT DISTINCT ON (horse_id)
        horse_id,
        training_type AS latest_training_type,
        training_center_code AS latest_center, course_code AS latest_course
    FROM session_rows
    ORDER BY horse_id, session_number
), comparable_rows AS MATERIALIZED (
    SELECT
        s.*,
        ROW_NUMBER() OVER (
            PARTITION BY s.horse_id
            ORDER BY s.training_date DESC, s.training_time DESC NULLS LAST, s.session_number
        ) AS comparable_number
    FROM session_rows AS s
    JOIN latest_type AS lt
      ON lt.horse_id = s.horse_id
     AND lt.latest_training_type IS NOT DISTINCT FROM s.training_type
     AND lt.latest_center IS NOT DISTINCT FROM s.training_center_code
     AND lt.latest_course IS NOT DISTINCT FROM s.course_code
), stats AS MATERIALIZED (
    SELECT
        horse_id,
        COUNT(*) AS valid_four_furlongs_count,
        AVG(four_furlongs_seconds) AS average_four_furlongs_seconds,
        STDDEV_POP(four_furlongs_seconds) AS deviation_four_furlongs_seconds,
        AVG(four_furlongs_seconds) FILTER (WHERE comparable_number <= 2) AS recent_average_four_furlongs_seconds,
        AVG(four_furlongs_seconds) FILTER (WHERE comparable_number > 2) AS older_average_four_furlongs_seconds
    FROM comparable_rows
    GROUP BY horse_id
), latest AS MATERIALIZED (
    SELECT DISTINCT ON (horse_id)
        horse_id,
        training_date,
        training_center_code,
        four_furlongs_seconds
    FROM comparable_rows
    ORDER BY horse_id, comparable_number
)
SELECT
    c.horse_number,
    c.horse_id,
    c.horse_name,
    COALESCE(stats.valid_four_furlongs_count, 0) AS valid_four_furlongs_count,
    latest.training_date AS latest_training_date,
    latest.training_center_code AS training_center,
    CASE
        WHEN stats.valid_four_furlongs_count IS NULL OR stats.valid_four_furlongs_count = 0 THEN NULL
        WHEN stats.deviation_four_furlongs_seconds IS NULL OR stats.deviation_four_furlongs_seconds = 0 THEN 5.0
        ELSE GREATEST(
            0.0,
            LEAST(
                10.0,
                ROUND((5.0 + ((stats.average_four_furlongs_seconds - latest.four_furlongs_seconds)
                    / stats.deviation_four_furlongs_seconds) * 2.0)::numeric, 1)::double precision
            )
        )
    END AS condition_score,
    CASE
        WHEN stats.valid_four_furlongs_count IS NULL OR stats.valid_four_furlongs_count = 0 THEN NULL
        WHEN stats.deviation_four_furlongs_seconds IS NULL OR stats.deviation_four_furlongs_seconds = 0
            OR stats.older_average_four_furlongs_seconds IS NULL THEN 5.0
        ELSE GREATEST(
            0.0,
            LEAST(
                10.0,
                ROUND((5.0 + ((stats.older_average_four_furlongs_seconds
                    - stats.recent_average_four_furlongs_seconds)
                    / stats.deviation_four_furlongs_seconds) * 2.0)::numeric, 1)::double precision
            )
        )
    END AS trend_score
FROM target_cards AS c
LEFT JOIN stats ON stats.horse_id = c.horse_id
LEFT JOIN latest ON latest.horse_id = c.horse_id
ORDER BY c.horse_number
LIMIT :limit
"""


def _rank(score: float | None) -> tuple[str | None, str]:
    """condition/trend平均をA〜Dの相対評価へ変換する。"""

    if score is None:
        return None, "比較可能な有効4F時計がありません（サンプル不足）"
    if score >= 8.0:
        return "A", "自己平均との差と直近傾向がともに強い"
    if score >= 6.0:
        return "B", "自己平均との差または直近傾向が良好"
    if score >= 4.0:
        return "C", "自己内で標準的な調教内容"
    return "D", "自己平均との差と直近傾向が弱い"


def _item_from_row(row: Mapping[str, object]) -> RaceTrainingEvaluationItem:
    """SQL集計行を公開itemへ変換し、内部スコア列を露出しない。"""

    condition = row.get("condition_score")
    trend = row.get("trend_score")
    values = [float(value) for value in (condition, trend) if isinstance(value, (int, float))]
    score = sum(values) / len(values) if values else None
    rank, reason = _rank(score)
    if rank is not None:
        reason = f"記述的評価: {reason}（有効4F {int(row.get('valid_four_furlongs_count') or 0)}本）"
    return RaceTrainingEvaluationItem(
        horse_number=display_horse_number(row.get("horse_number")),
        horse_name=str(row.get("horse_name") or ""),
        ketto_toroku_bango=str(row.get("horse_id") or ""),
        training_center=display_training_center(row.get("training_center")),
        rank=rank,
        rank_reason=reason + "。同種・同センター・同コースの時計比較であり、状態の良否は未判定。",
        comparable_session_count=int(row.get("valid_four_furlongs_count") or 0),
        missing_reason=None if score is not None else "no_comparable_HC_WC_clock",
        score_semantics=EVALUATION_SEMANTICS,
    )


def build_race_training_evaluation_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したrace_training_evaluation関数を作る。"""

    async def race_training_evaluation(race_code: RaceCode) -> RaceTrainingEvaluationOutput:
        """対象レースの直前21日調教を自己内相対でA〜D評価する。"""

        rows = await fetch_rows(database, _SQL, {"race_code": race_code, "limit": 100})
        items = [_item_from_row(row) for row in rows]
        return RaceTrainingEvaluationOutput(race_code=race_code, items=items, count=len(items))

    return race_training_evaluation
