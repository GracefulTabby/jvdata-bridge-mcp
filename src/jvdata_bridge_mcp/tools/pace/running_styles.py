"""出走馬の脚質分布を直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.pace._normalization import normalized_running_style_sql

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class RunningStyleItem(BaseModel):
    """対象レースの脚質別出走馬1件。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: str = Field(description="zero-padded馬番")
    horse_name: str = Field(description="馬名")
    ketto_toroku_bango: str = Field(description="血統登録番号")
    main_style: str | None = Field(default=None, description="対象日前の確定履歴から求めた代表脚質")
    previous_style: str | None = Field(default=None, description="前走の脚質")
    preferred_distance: str | None = Field(
        default=None,
        description="対象日前の距離別成績で複勝率が最も高い距離区分",
    )
    avg_positions_gained: float | None = Field(
        default=None,
        description="最初に記録されたコーナーから最後に記録されたコーナーまでの平均順位変化（正=前進）",
    )
    style_stats: dict[str, object] | None = Field(default=None, description="脚質別の過去成績")


class RacePaceRunningStylesOutput(BaseModel):
    """対象レースの脚質別出走馬一覧。"""

    result_policy: str = "complete_official_or_provisional; provisional_can_be_revised"

    race_code: str = Field(description="レースコード")
    items: list[RunningStyleItem] = Field(default_factory=list, description="馬別の代表脚質一覧")
    count: int = Field(default=0, description="脚質を返した頭数")
    style_method: str = Field(default="historical_main_style", description="脚質の意味")
    style_version: str = Field(default="bulk_running_style_v2", description="脚質分類ロジックのバージョン")


_NORMALIZED_RUNNING_STYLE = normalized_running_style_sql("h.running_style_code")
_NORMALIZED_RUNNING_STYLE_H2 = normalized_running_style_sql("h2.running_style_code")


_SQL = f"""
WITH target AS MATERIALIZED (
    SELECT DISTINCT ON (c.race_id, c.horse_number)
        c.race_id,
        c.horse_number,
        c.horse_id,
        c.horse_name,
        r.race_date
    FROM jv_mart.race_cards AS c
    JOIN jv_data.races AS r ON r.race_id = c.race_id
    WHERE c.race_id = :race_code
      AND c.horse_number IS NOT NULL

    ORDER BY c.race_id, c.horse_number, c.horse_id
)
SELECT
    t.horse_number,
    t.horse_id AS ketto_toroku_bango,
    t.horse_name,
    COALESCE(profile.main_style, history.main_style, 'unknown') AS main_style,
    {_NORMALIZED_RUNNING_STYLE} AS previous_style,
    history.preferred_distance,
    history.avg_positions_gained,
    history.style_stats
FROM target AS t
LEFT JOIN LATERAL (
    SELECT
        e.registered_race_count,
        e.front_runner_count,
        e.stalker_count,
        e.closer_count,
        e.deep_closer_count,
        CASE
            WHEN COALESCE(e.registered_race_count, 0) > 0
                THEN CASE
                    WHEN COALESCE(e.front_runner_count, 0) >= GREATEST(
                        COALESCE(e.stalker_count, 0),
                        COALESCE(e.closer_count, 0),
                        COALESCE(e.deep_closer_count, 0)
                    ) THEN 'front_runner'
                    WHEN COALESCE(e.stalker_count, 0) >= GREATEST(
                        COALESCE(e.closer_count, 0),
                        COALESCE(e.deep_closer_count, 0)
                    ) THEN 'stalker'
                    WHEN COALESCE(e.closer_count, 0) >= COALESCE(e.deep_closer_count, 0)
                        THEN 'closer'
                    ELSE 'deep_closer'
                END
        END AS main_style
    FROM jv_mart.entry_performances AS e
    WHERE e.race_id = t.race_id
      AND e.horse_id = t.horse_id
    ORDER BY e.source_sequence DESC NULLS LAST
    LIMIT 1
) AS profile ON TRUE
LEFT JOIN LATERAL (
    SELECT
        MODE() WITHIN GROUP (ORDER BY {_NORMALIZED_RUNNING_STYLE}) FILTER (
            WHERE {_NORMALIZED_RUNNING_STYLE} <> 'unknown'
        ) AS main_style,
        (
            SELECT distance_stats.distance_category
            FROM (
                SELECT
                    CASE
                        WHEN hd.distance_meters <= 1400 THEN '短距離'
                        WHEN hd.distance_meters <= 1800 THEN 'マイル'
                        WHEN hd.distance_meters <= 2400 THEN '中距離'
                        WHEN hd.distance_meters <= 2800 THEN '中長距離'
                        ELSE '長距離'
                    END AS distance_category,
                    COUNT(*) AS starts,
                    COUNT(*) FILTER (WHERE hd.finish_position <= 3) AS top3
                FROM jv_mart.horse_runs AS hd
                WHERE hd.horse_id = t.horse_id
                  AND {before_target_race_sql("hd", "t.race_date")}
                  AND (hd.is_final_result IS TRUE OR hd.result_status_code='provisional_complete')
                  AND hd.is_complete_result IS TRUE AND hd.is_starter IS TRUE
                  AND hd.distance_meters IS NOT NULL
                GROUP BY 1
            ) AS distance_stats
            WHERE distance_stats.top3 > 0
            ORDER BY
                distance_stats.top3::numeric / NULLIF(distance_stats.starts, 0) DESC,
                distance_stats.starts DESC,
                CASE distance_stats.distance_category
                    WHEN '短距離' THEN 1
                    WHEN 'マイル' THEN 2
                    WHEN '中距離' THEN 3
                    WHEN '中長距離' THEN 4
                    ELSE 5
                END
            LIMIT 1
        ) AS preferred_distance,
        AVG(
            CASE
                WHEN COALESCE(
                    h.corner_1_position,
                    h.corner_2_position,
                    h.corner_3_position,
                    h.corner_4_position
                ) IS NOT NULL
                 AND COALESCE(
                    h.corner_4_position,
                    h.corner_3_position,
                    h.corner_2_position,
                    h.corner_1_position
                ) IS NOT NULL
                    THEN COALESCE(
                        h.corner_1_position,
                        h.corner_2_position,
                        h.corner_3_position,
                        h.corner_4_position
                    ) - COALESCE(
                        h.corner_4_position,
                        h.corner_3_position,
                        h.corner_2_position,
                        h.corner_1_position
                    )
            END
        ) AS avg_positions_gained,
        (
            SELECT jsonb_object_agg(stats.running_style, jsonb_build_object(
                'starts', stats.starts,
                'wins', stats.wins,
                'seconds', stats.seconds,
                'thirds', stats.thirds,
                'win_rate', ROUND(stats.wins::numeric / NULLIF(stats.starts, 0) * 100, 3),
                'place_rate', ROUND((stats.wins + stats.seconds)::numeric / NULLIF(stats.starts, 0) * 100, 3),
                'show_rate', ROUND((stats.wins + stats.seconds + stats.thirds)::numeric / NULLIF(stats.starts, 0) * 100, 3)
            ))
            FROM (
                SELECT
                    {_NORMALIZED_RUNNING_STYLE_H2} AS running_style,
                    COUNT(*) AS starts,
                    COUNT(*) FILTER (WHERE h2.finish_position = 1) AS wins,
                    COUNT(*) FILTER (WHERE h2.finish_position = 2) AS seconds,
                    COUNT(*) FILTER (WHERE h2.finish_position = 3) AS thirds
                FROM jv_mart.horse_runs AS h2
                WHERE h2.horse_id = t.horse_id
                  AND {before_target_race_sql("h2", "t.race_date")}
                  AND (h2.is_final_result IS TRUE OR h2.result_status_code='provisional_complete')
                  AND h2.is_complete_result IS TRUE AND h2.is_starter IS TRUE
                  AND {_NORMALIZED_RUNNING_STYLE_H2} <> 'unknown'
                GROUP BY 1
            ) AS stats
        ) AS style_stats
    FROM jv_mart.horse_runs AS h
    WHERE h.horse_id = t.horse_id
      AND {before_target_race_sql("h", "t.race_date")}
      AND (h.is_final_result IS TRUE OR h.result_status_code='provisional_complete')
      AND h.is_complete_result IS TRUE AND h.is_starter IS TRUE
) AS history ON TRUE
LEFT JOIN LATERAL (
    SELECT
        h.running_style_code
    FROM jv_mart.horse_runs AS h
    WHERE h.horse_id = t.horse_id
      AND {before_target_race_sql("h", "t.race_date")}
      AND (h.is_final_result IS TRUE OR h.result_status_code='provisional_complete')
      AND h.is_complete_result IS TRUE AND h.is_starter IS TRUE
    ORDER BY h.race_date DESC, h.race_id DESC
    LIMIT 1
) AS h ON TRUE
ORDER BY t.horse_number
LIMIT :limit
"""


_STYLE_LABELS = {
    "front_runner": "逃げ",
    "stalker": "先行",
    "closer": "差し",
    "deep_closer": "追込",
    "unknown": "不明",
}


def _style_label(value: object) -> str:
    """正規化した英字脚質を公開用の日本語値へ変換する。"""

    return _STYLE_LABELS.get(str(value or "unknown"), "不明")


def _style_stats(value: object) -> dict[str, object] | None:
    """SQLの脚質別集計を日本語の脚質別統計へ変換する。"""

    if not isinstance(value, dict):
        return None
    converted: dict[str, object] = {}
    for raw_style, raw_stats in value.items():
        if not isinstance(raw_stats, dict):
            continue
        converted[_style_label(raw_style)] = {
            "総数": int(raw_stats.get("starts") or 0),
            "1着": int(raw_stats.get("wins") or 0),
            "2着": int(raw_stats.get("seconds") or 0),
            "3着": int(raw_stats.get("thirds") or 0),
            "勝率": float(raw_stats.get("win_rate") or 0.0),
            "連対率": float(raw_stats.get("place_rate") or 0.0),
            "複勝率": float(raw_stats.get("show_rate") or 0.0),
        }
    return converted or None


def _item(row: dict[str, object]) -> RunningStyleItem:
    """SQL行を馬別の脚質集計と位置履歴へ変換する。"""

    horse_number = row.get("horse_number")
    return RunningStyleItem(
        horse_number=f"{int(horse_number):02d}",
        horse_name=str(row.get("horse_name") or ""),
        ketto_toroku_bango=str(row.get("ketto_toroku_bango") or ""),
        main_style=_style_label(row.get("main_style")),
        previous_style=_style_label(row.get("previous_style")),
        preferred_distance=str(row.get("preferred_distance") or "不明"),
        avg_positions_gained=(
            float(row["avg_positions_gained"]) if row.get("avg_positions_gained") is not None else None
        ),
        style_stats=_style_stats(row.get("style_stats")),
    )


def build_race_pace_running_styles_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したrace_pace_running_styles関数を作る。"""

    async def race_pace_running_styles(race_code: RaceCode) -> RacePaceRunningStylesOutput:
        """対象レースの出走馬の脚質分布を取得する。

        出走馬ごとに、対象レースより前の確定過去走から最新の脚質とコーナー位置を
        馬番順で返す。対象レース自身の結果は参照しない。

        Args:
            race_code: 対象レースのレースコード。

        Returns:
            出走馬の脚質事実。
        """

        rows = await fetch_rows(
            database,
            _SQL,
            {"race_code": race_code, "limit": 100},
        )
        items = [_item(row) for row in rows]
        return RacePaceRunningStylesOutput(race_code=race_code, items=items, count=len(items))

    return race_pace_running_styles
