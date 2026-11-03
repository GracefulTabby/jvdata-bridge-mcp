"""学習と同じ事前日特徴量のライブ取得。対象結果へのJOINは持たない。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.ml.features import (
    CARD_FIELDS,
    add_field_ranks,
    day,
    enrich_run,
    mean,
    number,
    runner_features,
    valid_run,
)
from jvdata_bridge_mcp.tools._race_boundary import RaceBoundary, resolve_race_boundary
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.race._entry_changes import (
    ENTRY_CHANGES_SQL,
    CurrentEntryObservations,
    current_entry_observations,
)

CARDS_SQL = """SELECT race_id,race_date,horse_id,horse_number,horse_name,horse_age,sex_code,jockey_id,trainer_id,
assigned_weight_kg,venue_code,track_code,distance_meters,grade_code,frame_number
FROM jv_mart.prediction_race_cards WHERE race_id=:race_code ORDER BY horse_number"""
HISTORY_SQL = """WITH histories AS MATERIALIZED (
 SELECT h.* FROM jv_mart.horse_runs h WHERE h.horse_id=ANY(CAST(:horses AS text[]))
 AND h.race_date<CAST(:cutoff AS date) AND h.race_date>=DATE '2010-01-01'
 AND h.race_date<=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Tokyo')::date
), race_ids AS MATERIALIZED (SELECT DISTINCT race_id FROM histories),
 race_details AS MATERIALIZED (
 SELECT r.race_id,r.starters,r.first_three_furlongs_seconds FROM jv_data.races r
 WHERE r.race_id IN (SELECT race_id FROM race_ids) AND r.venue_code BETWEEN '01' AND '10'
), cards AS MATERIALIZED (
 SELECT c.race_id,c.horse_id,c.jockey_id,c.trainer_id,c.assigned_weight_kg,c.horse_age
 FROM jv_mart.prediction_race_cards c WHERE c.horse_id=ANY(CAST(:horses AS text[]))
 AND c.race_id IN (SELECT race_id FROM race_ids)
), closing AS MATERIALIZED (
 SELECT p.race_id,p.horse_id,
 (COUNT(*) OVER (PARTITION BY p.race_id)-RANK() OVER (PARTITION BY p.race_id ORDER BY p.last_three_furlongs_seconds)
  -0.5*(COUNT(*) OVER (PARTITION BY p.race_id,p.last_three_furlongs_seconds)-1))::float
  /NULLIF(COUNT(*) OVER (PARTITION BY p.race_id)-1,0) AS closing_quality
 FROM jv_mart.race_results p WHERE p.race_id IN (SELECT race_id FROM race_ids)
 AND p.is_final_result AND p.is_complete_result AND p.is_starter AND p.is_finisher
 AND p.last_three_furlongs_seconds IS NOT NULL
)
SELECT h.*,r.starters,c.jockey_id,c.trainer_id,c.assigned_weight_kg,c.horse_age AS feature_horse_age,
 r.first_three_furlongs_seconds,q.closing_quality
FROM histories h JOIN race_details r ON r.race_id=h.race_id
JOIN cards c ON c.race_id=h.race_id AND c.horse_id=h.horse_id
LEFT JOIN closing q ON q.race_id=h.race_id AND q.horse_id=h.horse_id
ORDER BY h.race_date DESC,h.race_id DESC,h.horse_number DESC"""
PEOPLE_SQL = """WITH candidates AS MATERIALIZED (
 SELECT c.horse_id,c.race_id,c.race_date,c.jockey_id,c.trainer_id,
 ROW_NUMBER() OVER (PARTITION BY c.{person}_id ORDER BY c.race_date DESC,c.race_id DESC,c.horse_number DESC) AS recency
 FROM jv_mart.prediction_race_cards c
 WHERE c.{person}_id=ANY(CAST(:people AS text[])) AND c.race_date<CAST(:cutoff AS date)
  AND c.race_date<=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Tokyo')::date
  AND c.venue_code BETWEEN '01' AND '10'
  AND c.race_date>=CAST(:cutoff AS date)-INTERVAL '1095 days'
), recent AS MATERIALIZED (SELECT * FROM candidates WHERE recency<=50),
 race_ids AS MATERIALIZED (SELECT DISTINCT race_id FROM recent),
 details AS MATERIALIZED (
 SELECT race_id,starters,distance_meters,track_code FROM jv_data.races
 WHERE race_id IN (SELECT race_id FROM race_ids)
), results AS MATERIALIZED (
 SELECT race_id,horse_id,finish_position,is_final_result,is_complete_result,is_starter,is_finisher
 FROM jv_data.race_results WHERE race_id IN (SELECT race_id FROM race_ids)
)
SELECT c.*,r.starters,x.finish_position,x.is_final_result,x.is_complete_result,x.is_starter,x.is_finisher,
r.distance_meters,r.track_code
FROM recent c JOIN details r ON r.race_id=c.race_id
LEFT JOIN results x ON x.race_id=c.race_id AND x.horse_id=c.horse_id
ORDER BY c.race_date DESC,c.race_id DESC,c.recency"""
TRAINING_SQL = """SELECT horse_id,training_date,four_furlongs_seconds,lap_200_0_seconds,quality_status,
 training_type,training_center_code,course_code,lap_400_200_seconds
FROM jv_data.training_sessions WHERE horse_id=ANY(CAST(:horses AS text[]))
 AND training_date<LEAST(CAST(:cutoff AS date),(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Tokyo')::date)
 AND training_date>=CAST(:cutoff AS date)-INTERVAL '180 days'"""
PACE_SQL = """SELECT first_three_furlongs_seconds FROM jv_data.races
WHERE venue_code=:venue AND LEFT(track_code,1)=:surface AND distance_meters=:distance
 AND race_date<CAST(:cutoff AS date) AND race_date>=DATE '2010-01-01'
 AND race_date<=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Tokyo')::date
 AND is_complete_result AND result_status_code='official' AND first_three_furlongs_seconds BETWEEN 25 AND 50
ORDER BY race_date DESC,race_id DESC LIMIT 400"""
SPEED_SQL = """SELECT horse_id,speed_quality_raw,speed_quality_coverage,speed_observation_count,speed_baseline_level
FROM jv_mart.horse_speed_quality WHERE race_id=:race_code AND race_date=CAST(:cutoff AS date)
 AND model_version='temporal-linear-v3-speed-2026-holdout'"""
OPPONENT_SQL = """WITH peers AS MATERIALIZED (
 SELECT c.race_id,c.horse_id,c.race_date FROM jv_mart.prediction_race_cards c
 JOIN jv_data.race_results x ON x.race_id=c.race_id AND x.horse_id=c.horse_id
  AND x.is_starter AND x.is_complete_result AND x.is_final_result
  AND x.race_id=ANY(CAST(:past_races AS text[]))
 WHERE c.race_id=ANY(CAST(:past_races AS text[]))
), horse_results AS MATERIALIZED (
 SELECT h.horse_id,h.race_id,h.race_date,h.finish_position FROM jv_mart.horse_runs h
 WHERE h.horse_id IN (SELECT horse_id FROM peers) AND h.race_date<(SELECT MAX(race_date) FROM peers)
 AND h.race_date>=DATE '2010-01-01' AND h.is_final_result AND h.is_complete_result AND h.is_starter AND h.is_finisher
), race_sizes AS MATERIALIZED (
 SELECT r.race_id,r.starters FROM jv_data.races r WHERE r.race_id IN (SELECT race_id FROM horse_results)
 AND r.venue_code BETWEEN '01' AND '10'
), qualities AS MATERIALIZED (
 SELECT h.*,(r.starters-h.finish_position)::float/NULLIF(r.starters-1,0) AS quality
 FROM horse_results h JOIN race_sizes r ON r.race_id=h.race_id
), strengths AS MATERIALIZED (
 SELECT p.race_id,p.horse_id,(COALESCE(SUM(q.quality*POWER(0.72,q.recency-1)),0)+0.75)
  /(COALESCE(SUM(POWER(0.72,q.recency-1)),0)+1.5) AS strength
 FROM peers p LEFT JOIN LATERAL (
  SELECT h.quality,
   ROW_NUMBER() OVER (ORDER BY h.race_date DESC,h.race_id DESC) AS recency
  FROM qualities h WHERE h.horse_id=p.horse_id AND h.race_date<p.race_date
  ORDER BY h.race_date DESC,h.race_id DESC LIMIT 5
 ) q ON TRUE GROUP BY p.race_id,p.horse_id
)
SELECT race_id,horse_id,(SUM(strength) OVER (PARTITION BY race_id)-strength)
 /NULLIF(COUNT(*) OVER (PARTITION BY race_id)-1,0) AS opponent_strength FROM strengths"""


async def current_roster(
    database: JvDataDatabase, race_code: str
) -> tuple[list[dict[str, Any]], RaceBoundary, CurrentEntryObservations]:
    boundary = await resolve_race_boundary(database, race_code)
    cards = await fetch_rows(database, CARDS_SQL, {"race_code": race_code})
    if not cards or len({c["horse_number"] for c in cards}) != len(cards):
        raise ValueError("unique prediction cards are required")
    changes = await fetch_rows(
        database,
        ENTRY_CHANGES_SQL,
        {
            "race_code": race_code,
            "analysis_local": boundary.analysis_at,
            "scheduled_start_local": boundary.scheduled_start_at,
        },
    )
    observations = current_entry_observations(
        cards, changes, analysis_at=boundary.analysis_at, scheduled_start_at=boundary.scheduled_start_at
    )
    runners = {r.horse_number: r for r in observations.runners}
    active = []
    for card in cards:
        runner = runners[int(card["horse_number"])]
        if runner.betting_eligible is False:
            continue
        card["jockey_id"], card["assigned_weight_kg"] = runner.jockey_id, runner.assigned_weight_kg
        active.append(card)
    return active, boundary, observations


async def live_features(
    database: JvDataDatabase, race_code: str, *, feature_recipe: str = "reference", extended: bool = False
) -> tuple[list[dict[str, Any]], list[dict[str, float]], RaceBoundary, dict[str, Any]]:
    active, boundary, observations = await current_roster(database, race_code)
    runners = {r.horse_number: r for r in observations.runners}
    if not 3 <= len(active) <= 18 or any(str(c.get("track_code") or "")[:1] not in {"1", "2"} for c in active):
        raise ValueError("ability model supports 3–18 active runners in JRA flat turf/dirt races")
    cutoff = day(active[0]["race_date"])
    parameters = {"race_code": race_code, "horses": [c["horse_id"] for c in active], "cutoff": str(cutoff)}
    history = await fetch_rows(database, HISTORY_SQL, parameters, statement_timeout_seconds=60, disable_jit=True)
    horse_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in history:
        run = enrich_run(r)
        if not valid_run(run):
            run["finish_quality"] = float("nan")
        horse_history[str(r["horse_id"])].append(run)
    from jvdata_bridge_mcp.ml.features import FEATURE_RECIPES

    recent_count = FEATURE_RECIPES[feature_recipe].recent_runs
    past_ids = sorted(
        {r["race_id"] for rows in horse_history.values() for r in [x for x in rows if valid_run(x)][:recent_count]}
    )
    opponent = (
        await fetch_rows(
            database, OPPONENT_SQL, {"past_races": past_ids}, statement_timeout_seconds=60, disable_jit=True
        )
        if past_ids
        else []
    )
    strengths = {(r["race_id"], r["horse_id"]): r["opponent_strength"] for r in opponent}
    for rows in horse_history.values():
        for run in rows:
            run["opponent_strength"] = strengths.get((run["race_id"], run["horse_id"]))
    people = {}
    for person in ("jockey", "trainer"):
        ids = sorted({str(c[f"{person}_id"]) for c in active if c.get(f"{person}_id")})
        rows = await fetch_rows(
            database,
            PEOPLE_SQL.format(person=person),
            {"people": ids, "cutoff": str(cutoff)},
            statement_timeout_seconds=60,
            disable_jit=True,
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            run = enrich_run(r)
            if not valid_run(run):
                run["finish_quality"] = float("nan")
            grouped[str(r[f"{person}_id"])].append(run)
        people[person] = grouped
    training = await fetch_rows(database, TRAINING_SQL, parameters, statement_timeout_seconds=60, disable_jit=True)
    sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in training:
        sessions[str(row["horse_id"])].append(row)
    speed = (
        await fetch_rows(database, SPEED_SQL, parameters, statement_timeout_seconds=10)
        if await database.relation_exists("jv_mart", "horse_speed_quality")
        else []
    )
    speeds = {r["horse_id"]: r for r in speed}
    first = active[0]
    pace_rows = await fetch_rows(
        database,
        PACE_SQL,
        {
            "cutoff": str(cutoff),
            "venue": first["venue_code"],
            "surface": str(first["track_code"])[:1],
            "distance": first["distance_meters"],
        },
    )
    pace = [number(r["first_three_furlongs_seconds"]) for r in reversed(pace_rows)]
    features = [
        runner_features(
            {k: v for k, v in card.items() if k in CARD_FIELDS},
            horse_history[str(card["horse_id"])],
            people["jockey"].get(str(card["jockey_id"]), []),
            people["trainer"].get(str(card["trainer_id"]), []),
            speeds.get(card["horse_id"]),
            sessions.get(str(card["horse_id"]), []),
            field_size=len(active),
            pace_history=pace,
            feature_recipe=feature_recipe,
        )
        for card in active
    ]
    add_field_ranks(features)
    extension_provenance = {}
    if extended:
        from jvdata_bridge_mcp.ml.expanded_live import augment

        extension_provenance = await augment(database, active, features, horse_history, sessions)
    summaries = {}
    for card in active:
        h = [r for r in horse_history[str(card["horse_id"])] if valid_run(r)]
        summaries[card["horse_id"]] = {
            "starts": len(h),
            "wins": sum(number(r.get("finish_position")) == 1 for r in h),
            "top3": sum(number(r.get("finish_position"), 99) <= 3 for r in h),
            "average_finish_position": mean([number(r.get("finish_position")) for r in h]),
            "average_last_three_furlongs": mean([number(r.get("last_three_furlongs_seconds")) for r in h]),
        }
    return (
        active,
        features,
        boundary,
        {
            "current_entry_observations": observations.model_dump(),
            "summaries": summaries,
            "excluded_horse_numbers": sorted(set(runners) - {int(c["horse_number"]) for c in active}),
            "card_version_timing": "stored_current_version; publication_time_unverified",
            "speed_missing_count": sum(f["speed_missing"] for f in features),
            **extension_provenance,
        },
    )
