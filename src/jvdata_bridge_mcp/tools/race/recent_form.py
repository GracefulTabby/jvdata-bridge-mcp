"""出走馬の直近成績を直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.pace._normalization import normalized_running_style_sql
from jvdata_bridge_mcp.tools.race._class_facts import OfficialClassFact, class_join_sql
from jvdata_bridge_mcp.tools.race._laps import lap_segment_lengths

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]
RecentRuns = Annotated[int, Field(description="集計対象にする直近走数")]


class RaceRecentFormItem(BaseModel):
    """対象レース出走馬の直近走1件。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: int | None = Field(default=None, description="馬番")
    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    recent_race_code: str | None = Field(default=None, description="直近レースコード")
    recent_race_date: str | None = Field(default=None, description="直近レース開催日")
    recent_racecourse_code: str | None = Field(default=None, description="直近競馬場コード")
    recent_race_name: str | None = Field(default=None, description="直近レース名")
    recent_class_name: str | None = Field(
        default=None, description="過去レースの年齢別条件クラス名。グレードはclass_factに分離"
    )
    class_fact: OfficialClassFact | None = None
    fact_id: str | None = None
    track_condition: str | None = None
    horse_age: int | None = None
    assigned_weight_kg: float | None = None
    jockey_id: str | None = None
    jockey_name: str | None = None
    trainer_name: str | None = None
    body_weight_kg: int | None = None
    abnormality_code: str | None = None
    is_final_result: bool | None = None
    result_status_code: str | None = None
    is_finisher: bool | None = None
    margin_code: str | None = None
    winner_to_second_margin_seconds: float | None = None
    race_laps: list[float | None] | None = None
    track_code: str | None = None
    lap_segment_lengths_meters: list[int] | None = None
    lap_source: str = "RA:ハロンタイム; leading_remainder_then_200m_for_flat"
    source_record_id: int | str | None = None
    field_size: int | None = Field(default=None, description="過去レースの公式出走頭数")
    finish_position: int | None = Field(default=None, description="着順")
    elapsed_seconds: float | None = Field(default=None, description="走破タイム。秒")
    time_difference_seconds: float | None = Field(default=None, description="勝馬とのタイム差。秒")
    last_three_furlongs_seconds: float | None = Field(default=None, description="上がり3ハロン。秒")
    corner_1_position: int | None = Field(default=None, description="1コーナー通過順位")
    corner_2_position: int | None = Field(default=None, description="2コーナー通過順位")
    corner_3_position: int | None = Field(default=None, description="3コーナー通過順位")
    corner_4_position: int | None = Field(default=None, description="4コーナー通過順位")
    final_win_odds: float | None = Field(default=None, description="最終単勝オッズ")
    final_win_popularity: int | None = Field(default=None, description="最終単勝人気")
    distance_meters: int | None = Field(default=None, description="距離。m")
    track_name: str | None = Field(default=None, description="走路名")
    running_style_code: str | None = Field(default=None, description="データソースの脚質コード")
    running_style: str = Field(description="正規化脚質")
    run_number: int | None = Field(default=None, description="対象馬内の直近走順位")
    selection_reasons: list[str] = Field(default_factory=list)


class RaceRecentFormFactsOutput(BaseModel):
    """対象レースの出走馬と直近走の事実。"""

    runs: list[RaceRecentFormItem] = Field(description="出走馬別の直近走事実")
    condition_runs: list[RaceRecentFormItem] = Field(
        default_factory=list, description="全収録期間から選んだ条件一致履歴。好走・凡走とも返す"
    )
    condition_history_coverage: dict[str, dict[str, object]] = Field(default_factory=dict)
    condition_history_limit: int = 0
    condition_history_scope: str = "all_stored_complete_JRA_starts_before_target_and_now; provisional_can_be_revised; recent_per_condition; not_positive_only"


_NORMALIZED_RUNNING_STYLE = normalized_running_style_sql("h.running_style_code")


_SQL = f"""
WITH target AS (
    SELECT race_id, race_date, venue_code, track_code, distance_meters
    FROM jv_data.races
    WHERE race_id = :race_code
), target_cards AS MATERIALIZED (
    SELECT DISTINCT ON (c.race_id, c.horse_number)
        c.horse_number, c.horse_id, c.horse_name
    FROM jv_mart.race_cards AS c
    WHERE c.race_id = :race_code
      AND c.horse_number IS NOT NULL

    ORDER BY c.race_id, c.horse_number, c.horse_id
), history_flags AS (
    SELECT c.horse_number AS target_horse_number, c.horse_name AS target_horse_name, h.*,
        CASE WHEN left(t.track_code,1) IN ('1','2') THEN left(h.track_code,1) = left(t.track_code,1)
             ELSE left(h.track_code,1) NOT IN ('1','2') END AS same_surface,
        h.distance_meters = t.distance_meters AS same_distance,
        h.venue_code = t.venue_code AND h.track_code = t.track_code AND h.distance_meters = t.distance_meters AS same_course_distance
    FROM target t
    JOIN target_cards c ON TRUE
    JOIN jv_mart.horse_runs h ON h.horse_id = c.horse_id
      AND {before_target_race_sql("h", "t.race_date")}
      AND (h.is_final_result IS TRUE OR h.result_status_code='provisional_complete') AND h.is_complete_result IS TRUE AND h.is_starter IS TRUE
      AND h.venue_code BETWEEN '01' AND '10'
), ranked_history AS (
    SELECT h.*,
        ROW_NUMBER() OVER (PARTITION BY horse_id ORDER BY race_date DESC, race_id DESC) AS run_number,
        ROW_NUMBER() OVER (PARTITION BY horse_id, same_surface ORDER BY race_date DESC, race_id DESC) AS surface_rank,
        ROW_NUMBER() OVER (PARTITION BY horse_id, same_surface AND same_distance ORDER BY race_date DESC, race_id DESC) AS distance_rank,
        ROW_NUMBER() OVER (PARTITION BY horse_id, same_course_distance ORDER BY race_date DESC, race_id DESC) AS course_rank,
        jsonb_build_object('all_stored_runs_searched', TRUE, 'stored_jra_starts', COUNT(*) OVER w,
            'same_surface_starts', COUNT(*) FILTER (WHERE same_surface) OVER w,
            'same_surface_distance_starts', COUNT(*) FILTER (WHERE same_surface AND same_distance) OVER w,
            'same_course_distance_starts', COUNT(*) FILTER (WHERE same_course_distance) OVER w,
            'evidence_start_date', MIN(race_date) OVER w, 'evidence_end_date', MAX(race_date) OVER w,
            'outside_jra_history', 'unknown') AS condition_coverage
    FROM history_flags h WINDOW w AS (PARTITION BY horse_id)
), selected_runs AS MATERIALIZED (
    SELECT * FROM ranked_history WHERE run_number <= :recent_runs OR (:condition_runs > 0 AND
        ((same_surface AND surface_rank <= :condition_runs) OR
         (same_surface AND same_distance AND distance_rank <= :condition_runs) OR
         (same_course_distance AND course_rank <= :condition_runs)))
), ranked_runs AS (
    SELECT
        h.target_horse_number AS horse_number,
        h.horse_id,
        h.target_horse_name AS horse_name,
        h.race_id AS recent_race_code,
        h.race_date AS recent_race_date,
        h.venue_code AS recent_racecourse_code,
        h.race_name AS recent_race_name,
        history_class.class_name AS recent_class_name,
        history_class.class_fact,
        'jra-run:' || h.race_id || ':' || h.horse_id AS fact_id,
        CASE WHEN h.track_name LIKE '%芝%' THEN pr.turf_condition_name ELSE pr.dirt_condition_name END AS track_condition,
        pc.horse_age, pc.assigned_weight_kg, pc.jockey_id, pc.jockey_name, pc.trainer_name, pc.body_weight_kg,
        h.abnormality_code, h.is_finisher, h.is_final_result, h.result_status_code, rr.margin_code, rr.source_record_id,
        CASE WHEN h.finish_position = 1 AND rr.is_finisher IS TRUE
             THEN second.elapsed_seconds - h.elapsed_seconds END AS winner_to_second_margin_seconds,
        lap.values AS race_laps, pr.track_code,
        pr.starters AS field_size,
        h.finish_position,
        h.elapsed_seconds,
        CASE WHEN h.finish_position = 1 THEN 0.0 ELSE h.time_difference_seconds END AS time_difference_seconds,
        h.last_three_furlongs_seconds,
        h.corner_1_position,
        h.corner_2_position,
        h.corner_3_position,
        h.corner_4_position,
        h.final_win_odds,
        h.final_win_popularity,
        h.distance_meters,
        h.track_name,
        h.running_style_code,
        {_NORMALIZED_RUNNING_STYLE} AS running_style,
        h.run_number,
        h.condition_coverage,
        array_remove(ARRAY[CASE WHEN h.run_number <= :recent_runs THEN 'recent' END,
            CASE WHEN :condition_runs > 0 AND h.same_surface AND h.surface_rank <= :condition_runs THEN 'same_surface' END,
            CASE WHEN :condition_runs > 0 AND h.same_surface AND h.same_distance AND h.distance_rank <= :condition_runs THEN 'same_surface_distance' END,
            CASE WHEN :condition_runs > 0 AND h.same_course_distance AND h.course_rank <= :condition_runs THEN 'same_course_distance' END], NULL) AS selection_reasons
    FROM selected_runs h
    LEFT JOIN jv_data.races AS pr ON pr.race_id = h.race_id
    LEFT JOIN LATERAL (
        SELECT horse_age, assigned_weight_kg, jockey_id, jockey_name, trainer_name, body_weight_kg
        FROM jv_mart.race_cards WHERE race_id = h.race_id AND horse_id = h.horse_id
        ORDER BY horse_number LIMIT 1
    ) pc ON TRUE
    {class_join_sql("pr", "history_class", "pc.horse_age")}
    LEFT JOIN LATERAL (
        SELECT margin_code, source_record_id, is_finisher FROM jv_mart.race_results r
        WHERE r.race_id = h.race_id AND r.horse_id = h.horse_id
          AND (r.is_final_result IS TRUE OR r.result_status_code='provisional_complete') AND r.is_complete_result IS TRUE
        ORDER BY r.source_record_id LIMIT 1
    ) rr ON TRUE
    LEFT JOIN LATERAL (
        SELECT MIN(elapsed_seconds) AS elapsed_seconds FROM jv_mart.race_results
        WHERE race_id = h.race_id AND finish_position = 2
          AND (is_final_result IS TRUE OR result_status_code='provisional_complete') AND is_complete_result IS TRUE AND is_finisher IS TRUE
    ) second ON TRUE
    LEFT JOIN LATERAL (
        SELECT array_agg(lap_seconds ORDER BY lap_number) AS values
        FROM jv_data.race_laps WHERE race_id = h.race_id
    ) lap ON TRUE
)
SELECT *
FROM ranked_runs
ORDER BY horse_number, run_number
LIMIT :limit
"""


def build_race_recent_form_facts_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したrace_recent_form_facts関数を作る。"""

    async def race_recent_form_facts(
        race_code: RaceCode,
        recent_runs: RecentRuns = 5,
        condition_runs: Annotated[
            int, Field(ge=0, le=20, description="全経歴の材質・距離・同場同走路一致から各条件の直近N件。0は取得しない")
        ] = 0,
    ) -> RaceRecentFormFactsOutput:
        """対象レースの出走馬について直近成績の事実を取得する。

        出走馬ごとに、対象レースより前の確定結果を新しい順で取得する。
        評価や推論は行わず、過去5走を標準に、クラス、頭数、着順、
        勝馬とのタイム差、上がり、コーナー通過順位、人気、距離、走路、脚質を返す。

        Args:
            race_code: 対象レースを識別するレースコード。
            recent_runs: 馬ごとに取得する直近走数。
            condition_runs: 全収録JRA歴から材質・材質と距離・同コース距離の各条件の直近N件。
                好走と凡走をともに返す。0は条件履歴を省略する。

        Returns:
            出走馬別の直近走事実。
        """

        bounded = max(1, min(recent_runs, 20))
        if not 0 <= condition_runs <= 20:
            raise ValueError("condition_runs must be 0–20")
        rows = await fetch_rows(
            database,
            _SQL,
            {"race_code": race_code, "recent_runs": bounded, "condition_runs": condition_runs, "limit": 10_000},
        )
        if len(rows) >= 10_000:
            raise ValueError("recent/condition history exceeded explicit row guard; request fewer condition_runs")
        coverage = {}
        for row in rows:
            coverage[str(row["horse_number"])] = row.pop("condition_coverage", {})
            lengths = lap_segment_lengths(row.get("distance_meters"), row.get("track_code"))
            row["lap_segment_lengths_meters"] = (
                lengths if lengths and len(row.get("race_laps") or []) == len(lengths) else None
            )
        return RaceRecentFormFactsOutput(
            runs=[r for r in rows if r["run_number"] <= bounded],
            condition_runs=[r for r in rows if any(x != "recent" for x in r.get("selection_reasons", []))],
            condition_history_coverage=coverage if condition_runs else {},
            condition_history_limit=condition_runs,
        )

    return race_recent_form_facts
