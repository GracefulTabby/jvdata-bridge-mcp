"""実際の当日・翌日の予想へ、当日の確認済み騎手成績を別枠で加える。"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Annotated, Callable, Literal

from pydantic import BaseModel, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import RaceBoundary, resolve_race_boundary
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools._source_timing import source_timing
from jvdata_bridge_mcp.tools.pace.course_profile import CourseProfileOptions
from jvdata_bridge_mcp.tools.prediction.status import PredictionModelStatusOutput, load_prediction_model_status
from jvdata_bridge_mcp.tools.race._entry_changes import (
    ENTRY_CHANGES_SQL,
    CurrentEntryObservations,
    current_entry_observations,
)
from jvdata_bridge_mcp.tools.race._weather import WEATHER_EVENTS_SQL, reconstruct_weather
from jvdata_bridge_mcp.tools.race._weight_source import WEIGHT_CTES, wh_natural_key
from jvdata_bridge_mcp.tools.race.forecast_context import (
    Observation,
    RaceForecastContextOutput,
    Section,
)
from jvdata_bridge_mcp.tools.race.forecast_context import (
    build_race_forecast_context_tool as build_race_facts_context_tool,
)

RaceCode = Annotated[str, Field(description="未発走の対象レースを識別する16桁コード")]


class LiveForecastContextOutput(BaseModel):
    race_code: str
    horizon: Literal["today", "tomorrow", "future"]
    analysis_at: str
    boundary: RaceBoundary
    race_context: RaceForecastContextOutput
    current_entry_observations: CurrentEntryObservations
    model_availability: PredictionModelStatusOutput
    current_observation_coverage: dict[str, object]
    today_jockey_results: list[dict[str, object]] = Field(default_factory=list)
    today_horse_weight_observations: list[dict[str, object]] = Field(default_factory=list)
    today_weather_observations: list[dict[str, object]] = Field(default_factory=list)
    today_result_policy: str = "complete_official_or_provisional_results; provisional_can_be_revised"
    same_day_overlap_race_codes: list[str] = Field(default_factory=list)
    same_day_evidence_already_in_base: bool
    market_included: bool = False
    target_results_included: bool = False
    information_policy: str = "completed_results_before_target_and_now; no_historical_replay"
    limitations: list[str] = Field(default_factory=list)


_JOCKEY_DAY_SQL = """
WITH target_cards AS MATERIALIZED (
    SELECT DISTINCT ON (c.race_id, c.horse_number) c.race_id, c.horse_number,
        COALESCE(jc.jockey_after_id,c.jockey_id) AS jockey_id,
        CASE WHEN jc.jockey_after_id IS NOT NULL THEN jc.jockey_after_name ELSE c.jockey_name END AS jockey_name
    FROM jv_mart.race_cards c JOIN jv_data.races target ON target.race_id=c.race_id
    LEFT JOIN LATERAL (
        SELECT jockey_after_id,jockey_after_name FROM jv_mart.race_changes
        WHERE race_id=c.race_id AND horse_number=c.horse_number AND source_record_type='JC'
          AND jockey_after_id IS NOT NULL
          AND announced_at<=CAST(:analysis_at AS timestamptz) AT TIME ZONE 'Asia/Tokyo'
          AND (target.scheduled_start_at IS NULL OR announced_at<target.scheduled_start_at)
        ORDER BY announced_at DESC,artifact_hash DESC LIMIT 1
    ) jc ON TRUE
    WHERE c.race_id = :race_code AND c.horse_number BETWEEN 1 AND 18
    ORDER BY c.race_id, c.horse_number
), target_jockeys AS MATERIALIZED (
    SELECT DISTINCT ON (jockey_id) jockey_id, jockey_name
    FROM target_cards
    WHERE jockey_id IS NOT NULL
    ORDER BY jockey_id, horse_number
), day_entries AS MATERIALIZED (
    SELECT DISTINCT ON (e.race_id, e.horse_id)
        e.jockey_id, e.jockey_name, e.race_id, e.horse_id, e.horse_name, e.venue_code
    FROM jv_mart.entry_performances e
    WHERE e.race_date = CAST(:today AS date) AND e.venue_code BETWEEN '01' AND '10'
    ORDER BY e.race_id, e.horse_id, e.source_sequence DESC NULLS LAST
), completed AS MATERIALIZED (
    SELECT DISTINCT ON (e.jockey_id, e.race_id, e.horse_id)
        e.jockey_id, e.jockey_name, e.race_id, e.horse_id, e.horse_name,
        rr.finish_position, rr.is_finisher, rr.abnormality_code,
        rr.final_win_popularity,
        rr.result_stage_code,rr.result_status_code,rr.is_final_result,rr.is_complete_result,
        r.scheduled_start_at
    FROM day_entries e
    JOIN target_jockeys j ON j.jockey_id = e.jockey_id
    JOIN jv_mart.race_results rr ON rr.race_id = e.race_id AND rr.horse_id = e.horse_id
      AND rr.race_date=CAST(:today AS date)
      AND rr.is_complete_result IS TRUE AND rr.is_starter IS TRUE
      AND rr.result_status_code IN ('official','provisional_complete')
    JOIN jv_data.races r ON r.race_id = e.race_id
    WHERE e.race_id <> :race_code
      AND r.scheduled_start_at < (CAST(:analysis_at AS timestamptz) AT TIME ZONE 'Asia/Tokyo')
    ORDER BY e.jockey_id, e.race_id, e.horse_id, rr.source_record_id
)
SELECT j.jockey_id, j.jockey_name, c.race_id, c.horse_id, c.horse_name,
       c.finish_position, c.is_finisher, c.abnormality_code, c.final_win_popularity,
       c.result_stage_code,c.result_status_code,c.is_final_result,c.is_complete_result,
       c.scheduled_start_at,
       (SELECT COUNT(*) FROM day_entries d JOIN jv_data.races r ON r.race_id=d.race_id
        WHERE d.jockey_id=j.jockey_id AND d.race_id<>:race_code
          AND r.scheduled_start_at<(CAST(:analysis_at AS timestamptz) AT TIME ZONE 'Asia/Tokyo'))
          AS earlier_recorded_entries
FROM target_jockeys j LEFT JOIN completed c ON c.jockey_id=j.jockey_id
ORDER BY j.jockey_id, c.race_id, c.horse_id
"""

_LIVE_WEIGHT_SQL = f"""
WITH {WEIGHT_CTES}, target_cards AS MATERIALIZED (
    SELECT DISTINCT ON (race_id, horse_number) race_id, horse_number
    FROM jv_mart.race_cards
    WHERE race_id=:race_code AND horse_number BETWEEN 1 AND 18
    ORDER BY race_id, horse_number
)
SELECT c.horse_number, chosen.body_weight_kg, chosen.body_weight_change_kg,
       chosen.announced_at, chosen.source
FROM target_cards c
LEFT JOIN wh_weights raw ON raw.horse_number=c.horse_number
LEFT JOIN latest_weights typed ON typed.horse_number=c.horse_number
LEFT JOIN LATERAL (
    SELECT * FROM (VALUES
        (typed.body_weight_kg, typed.body_weight_change_kg, typed.announced_at, 'latest_horse_weights', 2),
        (raw.body_weight_kg, raw.body_weight_change_kg, NULL::timestamp, 'WH', 1)
    ) v(body_weight_kg, body_weight_change_kg, announced_at, source, priority)
    WHERE v.body_weight_kg IS NOT NULL
      AND (v.source <> 'WH' OR typed.body_weight_kg IS NULL)
      AND (v.announced_at IS NULL OR v.announced_at <= CAST(:analysis_local AS timestamp))
      AND (v.announced_at IS NULL OR v.announced_at < (
          SELECT scheduled_start_at FROM jv_data.races WHERE race_id=:race_code
      ))
    ORDER BY v.priority DESC LIMIT 1
) chosen ON TRUE
WHERE c.race_id=:race_code AND c.horse_number BETWEEN 1 AND 18
ORDER BY c.horse_number
"""

_LIVE_WEATHER_SQL = WEATHER_EVENTS_SQL


def _summarize(rows: list[dict[str, object]], *, analysis_at: str | None = None) -> list[dict[str, object]]:
    """騎乗数を分母とする実測値を返し、騎手の状態や能力点には変換しない。"""

    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        key = str(row["jockey_id"])
        item = grouped.setdefault(
            key,
            {
                "jockey_id": key,
                "jockey_name": row.get("jockey_name"),
                "runs": [],
                "earlier_recorded_entries": row.get("earlier_recorded_entries"),
            },
        )
        if row.get("race_id"):
            run = {k: v for k, v in row.items() if k not in {"jockey_id", "jockey_name"}}
            run["fact_id"] = f"jra-run:{row['race_id']}:{row['horse_id']}"
            if analysis_at:
                run["source_timing"] = source_timing(analysis_at).model_dump()
            item["runs"].append(run)
    for item in grouped.values():
        runs = item["runs"]
        n = len(runs)
        wins = sum(r.get("is_finisher") is True and r.get("finish_position") == 1 for r in runs)
        top3 = sum(
            r.get("is_finisher") is True
            and isinstance(r.get("finish_position"), int)
            and 1 <= r["finish_position"] <= 3
            for r in runs
        )
        finishers = [
            r["finish_position"]
            for r in runs
            if r.get("is_finisher") is True and isinstance(r.get("finish_position"), int)
        ]
        item.update(
            {
                "starts": n,
                "wins": wins,
                "top3": top3,
                "nonfinishers": sum(r.get("is_finisher") is False for r in runs),
                "win_rate": wins / n if n else None,
                "top3_rate": top3 / n if n else None,
                "mean_finish_position": sum(finishers) / len(finishers) if finishers else None,
                "condition_inference": "unassessed; descriptive_results_only",
                "result_coverage_status": "completed_results_available" if n else "no_completed_results_available",
                "zero_starts_semantics": "count_of_eligible_stored_results; not_proof_of_no_actual_rides",
                "provisional_starts": sum(r.get("is_final_result") is False for r in runs),
            }
        )
        recorded = item["earlier_recorded_entries"]
        item["recorded_entries_without_completed_start_result"] = (
            max(0, recorded - n) if isinstance(recorded, int) else None
        )
        item["coverage_limit"] = (
            "Unresolved entries may be nonstarters or delayed/missing results; they do not prove actual rides."
        )
    return list(grouped.values())


def build_race_forecast_context_tool(database: JvDataDatabase) -> Callable[..., object]:
    async def race_forecast_context(
        race_code: RaceCode,
        sections: list[Section] | None = None,
        include_position_branches: bool = False,
        training_history_limit: Annotated[int, Field(ge=0, le=100)] = 3,
        observations: list[Observation] | None = None,
        condition_history_limit: Annotated[int, Field(ge=0, le=20)] = 3,
        course_profile_options: CourseProfileOptions | None = None,
    ) -> LiveForecastContextOutput:
        """未発走レースの予想材料を、対象レースと現在時刻から自動で取得する。

        日時や当日・翌日のモード指定は不要。当日の先行レースの完全速報も含む。
        過去走・調教・位置・コース・血統と、現在の馬体重・天候馬場・騎手当日成績を返す。
        対象レースの結果と市場は含めない。市場は馬評価を固定してからodds_tableで取得する。
        過去時点の情報の再現は行わない。時刻不明の同日実績を前後関係で推測しない。
        発表時刻付きAV/JCの現在出走情報、観測の経過時間・被覆、モデルの事前計算状態も返す。
        既往証拠と現在の取消・除外は分け、通知がないことを購入可能の保証にしない。
        """
        boundary = await resolve_race_boundary(database, race_code)
        now = datetime.fromisoformat(boundary.analysis_at)
        target_date = date.fromisoformat(boundary.race_date)
        days = (target_date - now.date()).days
        horizon = "today" if days == 0 else "tomorrow" if days == 1 else "future"
        base, rows, changes, model_status = await asyncio.gather(
            build_race_facts_context_tool(database)(
                race_code,
                sections=sections,
                include_position_branches=include_position_branches,
                training_history_limit=training_history_limit,
                observations=observations,
                condition_history_limit=condition_history_limit,
                course_profile_options=course_profile_options,
            ),
            fetch_rows(
                database,
                _JOCKEY_DAY_SQL,
                {"race_code": race_code, "today": now.date().isoformat(), "analysis_at": boundary.analysis_at},
                disable_jit=True,
            ),
            fetch_rows(
                database,
                ENTRY_CHANGES_SQL,
                {
                    "race_code": race_code,
                    "analysis_local": now.replace(tzinfo=None).isoformat(),
                    "scheduled_start_local": datetime.fromisoformat(boundary.scheduled_start_at)
                    .replace(tzinfo=None)
                    .isoformat()
                    if boundary.scheduled_start_at
                    else None,
                },
            ),
            load_prediction_model_status(database, race_code, analysis_at=boundary.analysis_at),
        )
        weights, weather = [], []
        if horizon == "today":
            weights, weather = await asyncio.gather(
                fetch_rows(
                    database,
                    _LIVE_WEIGHT_SQL,
                    {
                        "race_code": race_code,
                        "wh_natural_key": wh_natural_key(race_code),
                        "analysis_local": now.replace(tzinfo=None).isoformat(),
                    },
                ),
                fetch_rows(
                    database,
                    _LIVE_WEATHER_SQL,
                    {
                        "race_code": race_code,
                        "today": now.date().isoformat(),
                        "analysis_local": now.replace(tzinfo=None).isoformat(),
                    },
                ),
            )
            weather = [reconstruct_weather(weather, analysis_at=boundary.analysis_at)] if weather else []
        for weight in weights:
            weight["source_timing"] = source_timing(
                boundary.analysis_at,
                published_at=weight.get("announced_at"),
                observed=bool(weight.get("source")),
            ).model_dump()
        entry_observations = current_entry_observations(
            base.entries, changes, analysis_at=boundary.analysis_at, scheduled_start_at=boundary.scheduled_start_at
        )
        weight_numbers = {int(w["horse_number"]) for w in weights if w.get("body_weight_kg") is not None}
        expected_weights = {r.horse_number for r in entry_observations.runners if r.betting_eligible is not False}
        missing_weights = sorted(expected_weights - weight_numbers)
        coverage = {
            "horse_weights": {
                "status": "not_target_day"
                if horizon != "today"
                else "not_applicable"
                if not expected_weights and base.entries
                else "complete"
                if not missing_weights and expected_weights
                else "partial"
                if weight_numbers
                else "unavailable",
                "observed_entries": len(weight_numbers),
                "observed_running_entries": len(weight_numbers & expected_weights),
                "declared_entries": len(base.entries),
                "expected_running_entries": len(expected_weights),
                "confirmed_nonrunner_horse_numbers": [
                    r.horse_number for r in entry_observations.runners if r.betting_eligible is False
                ],
                "missing_horse_numbers": missing_weights,
                "missing_semantics": "no_numeric_current_weight; may_be_unannounced_nonrunner_or_unavailable",
            },
            "weather": {
                "status": str(weather[0].get("observation_status", "unavailable"))
                if weather
                else "not_target_day"
                if horizon != "today"
                else "unavailable",
                "field_times": "today_weather_observations[].field_observations.*.source_timing",
            },
            "entry_changes": {
                "status": entry_observations.coverage_status,
                "unverified_notices": entry_observations.unverified_notice_count,
            },
            "results": {
                "coverage_scope": "recorded_earlier_entries_for_current_target_jockeys",
                "unresolved_entries": "inspect_today_jockey_results.recorded_entries_without_completed_start_result",
                "upstream_coverage_verified": False,
            },
        }
        base_today_codes = {
            str(f.get("recent_race_code") or f.get("race_code") or "")
            for f in base.facts.values()
            if str(f.get("recent_race_date") or f.get("race_date") or "") == now.date().isoformat()
        }
        overlap = sorted(base_today_codes & {str(r["race_id"]) for r in rows if r.get("race_id")})
        return LiveForecastContextOutput(
            race_code=race_code,
            horizon=horizon,
            analysis_at=boundary.analysis_at,
            boundary=boundary,
            race_context=base,
            current_entry_observations=entry_observations,
            model_availability=model_status,
            current_observation_coverage=coverage,
            today_jockey_results=_summarize(rows, analysis_at=boundary.analysis_at),
            same_day_evidence_already_in_base=bool(overlap),
            same_day_overlap_race_codes=overlap,
            today_horse_weight_observations=weights,
            today_weather_observations=weather,
            limitations=[
                "Same-day results are a small, selected sample and do not establish jockey fitness or stable ability.",
                "Complete provisional results can be revised; partial top-3/top-5 results are excluded.",
                "Stored results can lag actual races; zero records does not mean no rides.",
                "Facts appearing in history and today's summaries must not be counted twice.",
                "Learned model features retain their prior-date aggregation contract; descriptive facts include eligible same-day results.",
                "Same-day training without a known time and same-day results without a known start remain excluded.",
                "Target odds and results are not included. Missing current weight/weather observations remain unknown.",
                "Observation age is reported per source; latest stored data does not verify upstream freshness or coverage.",
                "Retrieval and result-publication times are unknown; analysis time and scheduled starts are not substitutes.",
                "Use current_entry_observations for confirmed nonrunners and replacement jockeys; declared-history/model rosters retain all entries.",
            ],
        )

    return race_forecast_context
