"""コース別ペースプロフィールを直接SQLで取得するMCPツール。"""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.pace._configuration import course_configuration
from jvdata_bridge_mcp.tools.race._class_facts import class_join_sql, source_race_key_sql
from jvdata_bridge_mcp.tools.race._laps import lap_window_comparison

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class PaceCourseProfileItem(BaseModel):
    """対象条件の過去レースペースプロフィール1件。"""

    model_config = ConfigDict(extra="forbid")

    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    distance_meters: int | None = Field(default=None, description="距離。m")
    track_code: str | None = Field(default=None, description="走路コード")
    metric_valid_counts: dict[str, int] = Field(default_factory=dict)
    metric_standard_deviations: dict[str, float | None] = Field(default_factory=dict)
    evidence_start_date: str | None = None
    evidence_end_date: str | None = None
    opening_three_segment_meters: int | None = None
    opening_four_segment_meters: int | None = None
    source_field_semantics: str = "RA前3ハロン/前4ハロン; leading_3_or_4_recorded_segments"
    race_count: int = Field(description="対象レース数")
    average_first_three_furlongs: float | None = Field(default=None, description="平均前半3ハロン。秒")
    average_first_four_furlongs: float | None = Field(default=None, description="平均前半4ハロン。秒")
    average_last_three_furlongs: float | None = Field(default=None, description="平均上がり3ハロン。秒")
    average_last_four_furlongs: float | None = Field(default=None, description="平均上がり4ハロン。秒")
    average_starters: float | None = Field(default=None, description="平均出走頭数")
    reference_scope: str = "same_venue_track_distance; caller_filters; broad_age_class_unless_specified"
    lap_windows: dict[str, dict[str, object]] = Field(default_factory=dict)


class CourseProfileOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lookback_days: int = Field(default=0, ge=0)
    history_start_date: date | None = None
    ground: str = ""
    grade_code: str = ""
    condition_code: str = ""
    horse_age: int | None = Field(default=None, ge=2)
    include_matched_reference: bool = True
    use_current_configuration: bool = True


class RacePaceCourseProfileOutput(BaseModel):
    """対象条件の過去レースペースプロフィール。"""

    result_policy: str = "complete_official_or_provisional; provisional_can_be_revised"

    filters: dict[str, object] = Field(default_factory=dict)
    profiles: list[PaceCourseProfileItem] = Field(description="過去レースのペースプロフィール")
    matched_profiles: list[PaceCourseProfileItem] = Field(
        default_factory=list, description="公式年齢別条件とグレードが対象と一致する参照"
    )
    matched_reference_status: str = "not_requested"
    target_condition_signature: dict[str, object] = Field(default_factory=dict)
    course_configuration: dict[str, object] = Field(default_factory=dict)


_SQL = f"""
WITH target AS (
    SELECT venue_code, distance_meters, track_code, race_date
    FROM jv_data.races
    WHERE race_id = :race_code
)
SELECT
    r.venue_code AS racecourse_code,
    r.distance_meters,
    r.track_code,
    COUNT(DISTINCT r.race_id) AS race_count,
    MIN(r.race_date) AS evidence_start_date, MAX(r.race_date) AS evidence_end_date,
    CASE WHEN left(r.track_code,1) IN ('1','2') THEN
        CASE WHEN r.distance_meters % 200 = 0 THEN 600 ELSE r.distance_meters % 200 + 400 END END AS opening_three_segment_meters,
    CASE WHEN left(r.track_code,1) IN ('1','2') THEN
        CASE WHEN r.distance_meters % 200 = 0 THEN 800 ELSE r.distance_meters % 200 + 600 END END AS opening_four_segment_meters,
    jsonb_build_object('first_three', COUNT(NULLIF(r.first_three_furlongs_seconds,0)),
        'first_four', COUNT(NULLIF(r.first_four_furlongs_seconds,0)), 'last_three', COUNT(NULLIF(r.last_three_furlongs_seconds,0)),
        'last_four', COUNT(NULLIF(r.last_four_furlongs_seconds,0)), 'starters', COUNT(r.starters)) AS metric_valid_counts,
    jsonb_build_object('first_three', STDDEV_POP(NULLIF(r.first_three_furlongs_seconds,0)),
        'first_four', STDDEV_POP(NULLIF(r.first_four_furlongs_seconds,0)), 'last_three', STDDEV_POP(NULLIF(r.last_three_furlongs_seconds,0)),
        'last_four', STDDEV_POP(NULLIF(r.last_four_furlongs_seconds,0))) AS metric_standard_deviations,
    ROUND(AVG(NULLIF(r.first_three_furlongs_seconds,0))::numeric, 3) AS average_first_three_furlongs,
    ROUND(AVG(NULLIF(r.first_four_furlongs_seconds,0))::numeric, 3) AS average_first_four_furlongs,
    ROUND(AVG(NULLIF(r.last_three_furlongs_seconds,0))::numeric, 3) AS average_last_three_furlongs,
    ROUND(AVG(NULLIF(r.last_four_furlongs_seconds,0))::numeric, 3) AS average_last_four_furlongs,
    ROUND(AVG(r.starters)::numeric, 3) AS average_starters
FROM jv_data.races AS r
CROSS JOIN target AS t
WHERE r.venue_code = t.venue_code
  AND r.distance_meters = t.distance_meters
  AND r.track_code = t.track_code
  AND r.race_id <> :race_code
  AND {before_target_race_sql("r", "t.race_date")}
  AND r.is_complete_result IS TRUE AND r.starters > 0
  AND r.data_stage_code IN ('4','5','6','7')
  AND (:lookback_days = 0 OR r.race_date >= t.race_date - :lookback_days)
  AND (:history_start_date = '' OR r.race_date >= CAST(NULLIF(:history_start_date, '') AS date))
  AND (:ground = '' OR CASE WHEN r.track_name LIKE '%芝%' THEN r.turf_condition_name ELSE r.dirt_condition_name END = :ground)
  AND (:grade_code = '' OR r.grade_code = :grade_code)
  AND (CAST(:horse_age AS int) IS NULL OR EXISTS (SELECT 1 FROM jv_mart.race_cards c
       WHERE c.race_id = r.race_id AND c.horse_age = :horse_age))
  AND (:condition_code = '' OR EXISTS (SELECT 1 FROM jv_data.source_field_values s
       WHERE s.record_type = 'RA' AND s.natural_key =
          replace(jsonb_build_object('f004',substring(r.race_id FROM 1 FOR 4), 'f005',substring(r.race_id FROM 5 FOR 4),
              'f006',substring(r.race_id FROM 9 FOR 2), 'f007',substring(r.race_id FROM 11 FOR 2),
              'f008',substring(r.race_id FROM 13 FOR 2), 'f009',substring(r.race_id FROM 15 FOR 2))::text,' ','')
         AND s.field_name = '競走条件コード 最若年条件' AND s.value_text = :condition_code))
GROUP BY r.venue_code, r.distance_meters, r.track_code
"""

_SYMBOL_JOIN = f"""
LEFT JOIN LATERAL (
    SELECT s.value_text AS race_symbol_code, ref.name AS race_symbol_name
    FROM jv_data.source_field_values s
    LEFT JOIN jv_ref.code_2006_race_symbols ref ON ref.code = s.value_text
    WHERE s.record_type = 'RA' AND s.natural_key = {source_race_key_sql("r.race_id")}
      AND s.field_name = '競走記号コード'
    ORDER BY s.artifact_hash LIMIT 1
) symbol ON TRUE
"""
_TARGET_SQL = f"""
SELECT r.race_date, r.venue_code, r.track_code, r.grade_code, target_class.class_fact,
       symbol.race_symbol_code, symbol.race_symbol_name
FROM jv_data.races r {class_join_sql("r", "target_class")} {_SYMBOL_JOIN}
WHERE r.race_id = :race_code
"""
_MATCHED_SQL = _SQL.replace(
    "CROSS JOIN target AS t", "CROSS JOIN target AS t\n" + class_join_sql("r", "candidate_class") + _SYMBOL_JOIN
)
_MATCHED_SQL = _MATCHED_SQL.replace(
    "GROUP BY r.venue_code",
    """
  AND COALESCE(r.grade_code, '') = :target_grade_code
  AND (CAST(:target_female_only AS boolean) IS NULL OR (symbol.race_symbol_name LIKE '%牝%' AND symbol.race_symbol_name NOT LIKE '%牡%' AND symbol.race_symbol_name NOT LIKE '%せん%') = :target_female_only)
  AND (SELECT COALESCE(jsonb_object_agg(key, value), '{}'::jsonb)
       FROM jsonb_each(candidate_class.class_fact -> 'condition_codes_by_age')
       WHERE key <> 'youngest' AND value <> '"000"'::jsonb) = CAST(:target_condition_codes AS jsonb)
GROUP BY r.venue_code
""",
)


def female_only_symbol(label: object) -> bool | None:
    """牡・牝は牝馬限定ではない。公式コード表の表示から限定を読む。"""
    if not label:
        return None
    name = str(label)
    return "牝" in name and "牡" not in name and "せん" not in name


def build_race_pace_course_profile_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したrace_pace_course_profile関数を作る。"""

    async def race_pace_course_profile(
        race_code: RaceCode,
        lookback_days: int = 0,
        ground: str = "",
        grade_code: str = "",
        condition_code: str = "",
        horse_age: int | None = None,
        history_start_date: str = "",
        include_matched_reference: bool = True,
        use_current_configuration: bool = True,
    ) -> RacePaceCourseProfileOutput:
        """対象レースのコースにおけるペースプロフィールを取得する。

        同じ競馬場・距離・走路における、対象レースより前の過去レースだけを使って
        序盤と終盤の時計を集計する。対象レース自身と後続の結果は参照せず、
        発走時刻を確認した先行同日レースの完全な結果は含める。

        Args:
            race_code: 対象レースのレースコード。

        Returns:
            コース条件別のペース統計。
        """

        if lookback_days < 0:
            raise ValueError("lookback_days must be nonnegative")
        if history_start_date:
            date.fromisoformat(history_start_date)
        targets = await fetch_rows(database, _TARGET_SQL, {"race_code": race_code}, disable_jit=True)
        if not targets:
            raise ValueError("target course unavailable")
        target = targets[0]
        configuration = course_configuration(
            str(target["venue_code"]), str(target["track_code"]), str(target["race_date"])
        )
        starts = [history_start_date]
        if use_current_configuration:
            starts.append(str(configuration.get("history_start_date") or ""))
        effective_start = max(starts)
        signature = {
            k: v
            for k, v in (target.get("class_fact") or {}).get("condition_codes_by_age", {}).items()
            if k != "youngest" and v != "000"
        }
        female_only = female_only_symbol(target.get("race_symbol_name"))
        filters = {
            "lookback_days": lookback_days,
            "ground": ground,
            "grade_code": grade_code,
            "condition_code": condition_code,
            "horse_age": horse_age,
            "history_start_date": effective_start,
            "requested_history_start_date": history_start_date,
            "use_current_configuration": use_current_configuration,
            "age_semantics": "races_with_at_least_one_runner_of_requested_age",
            "condition_semantics": "youngest_age_condition_code",
        }
        rows = await fetch_rows(database, _SQL, {"race_code": race_code, **filters})
        matched, status = [], "not_requested"
        if include_matched_reference and signature:
            matched = await fetch_rows(
                database,
                _MATCHED_SQL,
                {
                    "race_code": race_code,
                    **filters,
                    "target_grade_code": target.get("grade_code") or "",
                    "target_condition_codes": json.dumps(signature),
                    "target_female_only": female_only,
                },
                disable_jit=True,
            )
            status = "available" if matched else "no_matching_past_races"
        elif include_matched_reference:
            status = "target_age_conditions_unknown"
        for row in rows + matched:
            row["lap_windows"] = {
                "three": lap_window_comparison(row.get("distance_meters"), row.get("track_code"), 3),
                "four": lap_window_comparison(row.get("distance_meters"), row.get("track_code"), 4),
            }
        for row in matched:
            row["reference_scope"] = (
                "same_venue_track_distance_and_official_age_condition_codes_and_grade_and_female_restriction; caller_filters; not_exact_runner_age_or_field_size"
            )
            if female_only is None:
                row["reference_scope"] += "; female_restriction_unknown"
        return RacePaceCourseProfileOutput(
            profiles=rows,
            matched_profiles=matched,
            matched_reference_status=status,
            target_condition_signature={
                "age_condition_codes": signature,
                "grade_code": target.get("grade_code"),
                "female_only": female_only,
                "race_symbol_code": target.get("race_symbol_code"),
                "race_symbol_name": target.get("race_symbol_name"),
            },
            course_configuration=configuration,
            filters=filters,
        )

    return race_pace_course_profile
