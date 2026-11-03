"""想定ペースの恩恵候補を、対象日前の確定履歴から抽出するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.pace._normalization import normalized_running_style_sql

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class PaceBeneficiaryItem(BaseModel):
    """脚質から見た展開恩恵候補。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: str = Field(default="", description="馬番")
    horse_name: str = Field(default="", description="馬名")
    main_style: str = Field(default="不明", description="主な脚質")
    average_positions_gained: float | None = Field(
        default=None,
        description="対象日前の確定過去走で、最初に記録されたコーナーから最終コーナーまでに押し上げた平均順位（正の値は順位上昇）",
    )
    average_fourth_corner_position: float | None = Field(
        default=None,
        description="対象日前の確定過去走における4コーナー平均通過順位",
    )


class JockeyPositionSpecialist(BaseModel):
    """位置取りの安定度から見た騎手・騎乗馬。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: str = Field(default="", description="馬番")
    horse_name: str = Field(default="", description="馬名")
    jockey_code: str | None = Field(default=None, description="騎手コード")
    jockey_name: str | None = Field(default=None, description="騎手名")
    position_stability: float | None = Field(
        default=None,
        description="1コーナーと4コーナーの通過順位の相関係数（-1〜1。高いほど位置取りが安定）",
    )
    position_stability_label: str | None = Field(default=None, description="位置取りの安定性評価")
    average_positions_gained: float | None = Field(
        default=None,
        description="対象日前の確定過去走で、最初に記録されたコーナーから最終コーナーまでに押し上げた平均順位（正の値は順位上昇）",
    )


class UncertainStyleHorse(BaseModel):
    """脚質履歴が十分でない出走馬。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: str = Field(default="", description="馬番")
    horse_name: str = Field(default="", description="馬名")
    main_style: str = Field(default="不明", description="判定された主な脚質")


class RacePaceIdentifyBeneficiariesOutput(BaseModel):
    """脚質・位置取りから見た展開恩恵候補。"""

    result_policy: str = "complete_official_or_provisional; provisional_can_be_revised"

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="対象レースコード")
    race_name: str | None = Field(default=None, description="対象レース名")
    closers: list[PaceBeneficiaryItem] = Field(
        default_factory=list,
        description="差し・追込で恩恵を受けやすい上位候補",
    )
    front_runners: list[PaceBeneficiaryItem] = Field(
        default_factory=list,
        description="逃げ・先行で恩恵を受けやすい上位候補",
    )
    positional_specialists: list[JockeyPositionSpecialist] = Field(
        default_factory=list,
        description="位置取り相関を計算できた騎手のうち、相対的に安定性が高い上位候補",
    )
    uncertain_style_horses: list[UncertainStyleHorse] = Field(
        default_factory=list,
        description="脚質履歴が十分でない出走馬",
    )
    count: int = Field(default=0, description="脚質が判定された展開恩恵候補数")


_NORMALIZED_RUNNING_STYLE = normalized_running_style_sql("h.running_style_code")


_SQL = f"""
WITH race_context AS MATERIALIZED (
    SELECT race_date, race_name
    FROM jv_data.races
    WHERE race_id = :race_code
), target AS MATERIALIZED (
    SELECT DISTINCT ON (c.race_id, c.horse_number)
        c.race_id,
        c.horse_number,
        c.horse_id,
        c.horse_name,
        c.jockey_id,
        c.jockey_name,
        r.race_date,
        r.race_name
    FROM jv_mart.race_cards AS c
    CROSS JOIN race_context AS r
    WHERE c.race_id = :race_code
      AND c.horse_number IS NOT NULL

    ORDER BY c.race_id, c.horse_number, c.horse_id
), target_jockeys AS MATERIALIZED (
    SELECT jockey_id, MIN(race_date) AS target_date
    FROM target
    WHERE jockey_id IS NOT NULL
    GROUP BY jockey_id
), jockey_runs AS MATERIALIZED (
    SELECT
        e.jockey_id,
        COALESCE(
            rr.corner_1_position,
            rr.corner_2_position,
            rr.corner_3_position,
            rr.corner_4_position
        ) AS first_recorded_corner_position,
        COALESCE(
            rr.corner_4_position,
            rr.corner_3_position,
            rr.corner_2_position,
            rr.corner_1_position
        ) AS last_recorded_corner_position,
        ROW_NUMBER() OVER (
            PARTITION BY e.jockey_id
            ORDER BY e.race_date DESC, e.race_id DESC
        ) AS history_rank
    FROM target_jockeys AS tj
    JOIN jv_mart.entry_performances AS e
      ON e.jockey_id = tj.jockey_id
     AND {before_target_race_sql("e", "tj.target_date")}
    JOIN jv_mart.race_results AS rr
      ON rr.race_id = e.race_id
     AND rr.horse_id = e.horse_id
    WHERE (rr.is_final_result IS TRUE OR rr.result_status_code='provisional_complete')
      AND rr.is_complete_result IS TRUE AND rr.is_starter IS TRUE
), jockey_stats AS (
    SELECT
        jockey_id,
        ROUND(
            AVG(first_recorded_corner_position - last_recorded_corner_position)::numeric,
            2
        ) AS average_positions_gained,
        ROUND(
            CORR(
                first_recorded_corner_position::double precision,
                last_recorded_corner_position::double precision
            )::numeric,
            3
        ) AS position_stability
    FROM jockey_runs
    WHERE history_rank <= 80
      AND first_recorded_corner_position IS NOT NULL
      AND last_recorded_corner_position IS NOT NULL
    GROUP BY jockey_id
)
SELECT
    t.race_id AS race_code,
    t.race_name,
    t.horse_number,
    t.horse_id,
    t.horse_name,
    t.jockey_id,
    t.jockey_name,
    COALESCE(profile.main_style, history.common_running_style, 'unknown') AS main_style,
    history.common_running_style,
    history.starts,
    history.average_last_three_furlongs,
    history.average_first_corner_position,
    history.average_fourth_corner_position,
    history.average_positions_gained,
    history.position_deviation,
    jockey_stats.average_positions_gained AS jockey_average_positions_gained,
    jockey_stats.position_stability AS jockey_position_stability
FROM target AS t
LEFT JOIN LATERAL (
    SELECT
        CASE
            WHEN COALESCE(e.registered_race_count, 0) > 0 THEN CASE
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
        ) AS common_running_style,
        COUNT(*)::int AS starts,
        AVG(h.last_three_furlongs_seconds) AS average_last_three_furlongs,
        AVG(h.corner_1_position) AS average_first_corner_position,
        AVG(h.corner_4_position) AS average_fourth_corner_position,
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
        ) AS average_positions_gained,
        STDDEV_POP(
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
        ) AS position_deviation
    FROM (
        SELECT
            h.*,
            ROW_NUMBER() OVER (ORDER BY h.race_date DESC, h.race_id DESC) AS history_rank
        FROM jv_mart.horse_runs AS h
        WHERE h.horse_id = t.horse_id
          AND {before_target_race_sql("h", "t.race_date")}
          AND (h.is_final_result IS TRUE OR h.result_status_code='provisional_complete')
          AND h.is_complete_result IS TRUE AND h.is_starter IS TRUE
    ) AS h
    WHERE h.history_rank <= 15
) AS history ON TRUE
LEFT JOIN jockey_stats
  ON jockey_stats.jockey_id = t.jockey_id
ORDER BY
    CASE WHEN COALESCE(profile.main_style, history.common_running_style, 'unknown') = 'unknown' THEN 1 ELSE 0 END,
    t.horse_number
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
    """共通英字ラベルを公開用の日本語脚質へ変換する。"""

    return _STYLE_LABELS.get(str(value or "unknown"), "不明")


def _float_or_none(value: object) -> float | None:
    """DBの数値を公開用floatへ変換する。"""

    return float(value) if isinstance(value, int | float) else None


def _stability(value: object) -> tuple[float | None, str | None]:
    """騎手の1C-4C相関係数と、その安定度ラベルを返す。"""

    deviation = _float_or_none(value)
    if deviation is None:
        return None, "不明"
    # race_development側の契約はCORR(1C, 4C) [-1, 1] をそのまま公開し、
    # 0.5を超える場合だけ「高」と判定する。
    return round(deviation, 3), "高" if deviation > 0.5 else "低"


def _beneficiary(row: dict[str, object], main_style: str) -> PaceBeneficiaryItem:
    """SQL行を脚質候補へ変換する。"""

    return PaceBeneficiaryItem(
        horse_number=f"{int(row['horse_number']):02d}",
        horse_name=str(row.get("horse_name") or ""),
        main_style=main_style,
        average_positions_gained=_float_or_none(row.get("average_positions_gained")),
        average_fourth_corner_position=_float_or_none(row.get("average_fourth_corner_position")),
    )


def _specialist(row: dict[str, object]) -> JockeyPositionSpecialist:
    """SQL行を位置取りスペシャリストへ変換する。"""

    stability, label = _stability(row.get("jockey_position_stability"))
    return JockeyPositionSpecialist(
        horse_number=f"{int(row['horse_number']):02d}",
        horse_name=str(row.get("horse_name") or ""),
        jockey_code=str(row["jockey_id"]) if row.get("jockey_id") is not None else None,
        jockey_name=str(row["jockey_name"]) if row.get("jockey_name") is not None else None,
        position_stability=stability,
        position_stability_label=label,
        average_positions_gained=_float_or_none(row.get("jockey_average_positions_gained")),
    )


def build_race_pace_identify_beneficiaries_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したrace_pace_identify_beneficiaries関数を作る。"""

    async def race_pace_identify_beneficiaries(
        race_code: RaceCode,
    ) -> RacePaceIdentifyBeneficiariesOutput:
        """脚質・位置変化・騎手の位置安定度から展開候補を分類する。"""

        rows = await fetch_rows(database, _SQL, {"race_code": race_code, "limit": 100})
        known: list[tuple[dict[str, object], str]] = []
        uncertain: list[UncertainStyleHorse] = []
        for row in rows:
            style = _style_label(row.get("main_style") or row.get("common_running_style"))
            if style == "不明":
                uncertain.append(
                    UncertainStyleHorse(
                        horse_number=f"{int(row['horse_number']):02d}",
                        horse_name=str(row.get("horse_name") or ""),
                        main_style=style,
                    )
                )
            else:
                known.append((row, style))

        # 候補カテゴリは上位3頭に絞る。前残り側は4C順位、
        # 差し側は最初→最終コーナーの押し上げ量をそれぞれ根拠にする。
        closers = sorted(
            [row for row, style in known if style in {"差し", "追込"}],
            key=lambda row: _float_or_none(row.get("average_positions_gained")) or 0.0,
            reverse=True,
        )[:3]
        front_runners = sorted(
            [row for row, style in known if style in {"逃げ", "先行"}],
            key=lambda row: _float_or_none(row.get("average_fourth_corner_position")) or 99.0,
        )[:3]
        specialist_candidates = [
            (row, stability)
            for row in rows
            if (stability := _stability(row.get("jockey_position_stability"))[0]) is not None
        ]
        specialists = [
            row for row, _ in sorted(specialist_candidates, key=lambda candidate: candidate[1], reverse=True)[:3]
        ]
        return RacePaceIdentifyBeneficiariesOutput(
            race_code=race_code,
            race_name=str(rows[0].get("race_name") or "") if rows else None,
            closers=[
                _beneficiary(row, _style_label(row.get("main_style") or row.get("common_running_style")))
                for row in closers
            ],
            front_runners=[
                _beneficiary(row, _style_label(row.get("main_style") or row.get("common_running_style")))
                for row in front_runners
            ],
            positional_specialists=[_specialist(row) for row in specialists],
            uncertain_style_horses=uncertain,
            count=len(closers) + len(front_runners),
        )

    return race_pace_identify_beneficiaries
