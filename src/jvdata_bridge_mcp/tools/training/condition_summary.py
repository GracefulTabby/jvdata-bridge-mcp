"""直前調教の自己内scoreと合成scoreの分布を要約するMCPツール。"""

from __future__ import annotations

from collections.abc import Mapping
from statistics import mean
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import training_before_target_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.training._normalization import (
    display_horse_number,
    valid_four_furlongs_sql,
    valid_training_quality_sql,
)

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]
Limit = Annotated[int, Field(description="上位・下位候補の最大件数")]

SUMMARY_SCORE_SEMANTICS = (
    "各スコアは0〜10に制限した自己内の記述指標で、予測性能を主張しない。"
    "raw_condition_scoreは直前21日の最新調教種別の最新有効4F時計を同種別の全期間平均・母標準偏差で標準化し、平均5、"
    "1標準偏差の短縮ごとに+2する。trend_scoreは同種別の直近2本平均とそれ以前の平均の差を同じ標準偏差で標準化し、"
    "短縮傾向を加点する。比較対象がないtrend_scoreは5、比較可能な調教記録自体がない場合は両スコアともnull。"
    "combined_scoreは利用可能なraw_condition_scoreとtrend_scoreの算術平均。サマリーの平均・分布・候補抽出・順位付けに使う。"
)
_VALID_FOUR_FURLONGS = valid_four_furlongs_sql("s.four_furlongs_seconds")
_VALID_QUALITY = valid_training_quality_sql("s")


class TrainingConditionSummaryOutput(BaseModel):
    """対象レースの調教condition score分布。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    average_score: float | None = Field(default=None, description="各馬のcombined_scoreの平均")
    highest_score: float | None = Field(default=None, description="combined_scoreの最高値")
    lowest_score: float | None = Field(default=None, description="combined_scoreの最低値")
    distribution: dict[str, int] = Field(description="combined_score帯別件数")
    high_performers: list[dict[str, object]] = Field(description="combined_score 8以上の上位馬")
    low_performers: list[dict[str, object]] = Field(description="combined_score 3以下の下位馬")
    selection: dict[str, object] = Field(description="候補抽出ルール")
    score_semantics: str = Field(description="サマリー集計値と各馬のスコア項目の意味")


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
        c.horse_id,
        s.training_date,
        s.training_time,
        s.training_type,
        s.training_center_code,s.course_code,
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
            s.training_center_code,s.course_code,
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
        four_furlongs_seconds
    FROM comparable_rows
    ORDER BY horse_id, comparable_number
)
SELECT
    c.horse_number,
    c.horse_id,
    c.horse_name,
    COALESCE(stats.valid_four_furlongs_count, 0) AS valid_four_furlongs_count,
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
LIMIT :row_limit
"""


def _number(row: Mapping[str, object]) -> str:
    """馬番を表示用の文字列へ変換する。"""

    return display_horse_number(row.get("horse_number"))


def _numeric(row: Mapping[str, object], key: str) -> float | None:
    """SQL値を有限のfloatへ変換する。"""

    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _performer(row: Mapping[str, object], score: float) -> dict[str, object]:
    """上位・下位候補に含めるスコアと最小限の事実を返す。"""

    return {
        "horse_number": _number(row),
        "horse_name": str(row.get("horse_name") or ""),
        "ketto_toroku_bango": str(row.get("horse_id") or ""),
        "combined_score": score,
        "raw_condition_score": _numeric(row, "condition_score"),
        "trend_score": _numeric(row, "trend_score"),
        "valid_four_furlongs_count": int(row.get("valid_four_furlongs_count") or 0),
    }


def _output(race_code: str, rows: list[Mapping[str, object]], limit: int) -> TrainingConditionSummaryOutput:
    """SQL行からscore分布と候補を構築する。"""

    scored: list[tuple[Mapping[str, object], float]] = []
    for row in rows:
        score_values = [
            float(value)
            for value in (row.get("condition_score"), row.get("trend_score"))
            if isinstance(value, (int, float))
        ]
        if score_values:
            scored.append((row, sum(score_values) / len(score_values)))
    scores = [score for _, score in scored]
    ordered = sorted(scored, key=lambda pair: (-pair[1], _number(pair[0])))
    high = [_performer(row, score) for row, score in ordered if score >= 8.0][: min(limit, 5)]
    low = [
        _performer(row, score)
        for row, score in sorted(scored, key=lambda pair: (pair[1], _number(pair[0])))
        if score <= 3.0
    ][: min(limit, 3)]
    return TrainingConditionSummaryOutput(
        race_code=race_code,
        average_score=round(mean(scores), 1) if scores else None,
        highest_score=max(scores) if scores else None,
        lowest_score=min(scores) if scores else None,
        distribution={
            "score_8_10": sum(score >= 8.0 for score in scores),
            "score_6_7": sum(6.0 <= score < 8.0 for score in scores),
            "score_4_5": sum(4.0 <= score < 6.0 for score in scores),
            "score_0_3": sum(score < 4.0 for score in scores),
            "scored_entries": len(scores),
        },
        high_performers=high,
        low_performers=low,
        selection={
            "score_field": "combined_score",
            "high_performers_rule": "combined_score_gte_8_top_5",
            "low_performers_rule": "combined_score_lte_3_bottom_3",
            "include_ties": False,
            "requested_limit": limit,
        },
        score_semantics=SUMMARY_SCORE_SEMANTICS,
    )


def build_training_condition_summary_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したtraining_condition_summary関数を作る。"""

    async def training_condition_summary(race_code: RaceCode, limit: Limit = 100) -> TrainingConditionSummaryOutput:
        """対象レースの直前21日combined_score分布と候補馬を返す。"""

        bounded_limit = max(1, min(limit, 100))
        rows = await fetch_rows(database, _SQL, {"race_code": race_code, "row_limit": 100})
        return _output(race_code, rows, bounded_limit)

    return training_condition_summary
