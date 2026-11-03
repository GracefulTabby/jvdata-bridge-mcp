"""拡張特徴量のライブ証拠取得。現在のカードと前日以前の証拠を区別する。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.ml._reference_sql import CLASS_SQL, TRAINING_REFERENCE_SQL
from jvdata_bridge_mcp.ml.expanded_features import (
    add_field_extensions,
    clock_values,
    official_class,
    runner_extensions,
    training_z,
)
from jvdata_bridge_mcp.ml.features import day, number, valid_run
from jvdata_bridge_mcp.tools._runtime import fetch_rows

CLASS_FOR_RACES_SQL = CLASS_SQL.replace(
    "r.race_date>=DATE '2010-01-01' AND r.race_date<CAST(:end AS date)",
    "r.race_id=ANY(CAST(:race_ids AS text[]))",
)
TRAINING_POPULATION_SQL = TRAINING_REFERENCE_SQL.replace(
    "DATE '2009-01-01'", "CAST(:cutoff AS date)-INTERVAL '545 days'"
).replace("CAST(:end AS date)", "CAST(:cutoff AS date)")

CLOCK_REFERENCE_SQL = """WITH requests AS MATERIALIZED (
 SELECT r.*,CASE WHEN LEFT(r.track_code,1)='1' THEN r.turf_condition_code ELSE r.dirt_condition_code END AS ground
 FROM jv_data.races r WHERE r.race_id=ANY(CAST(:past_races AS text[])) AND r.race_date<CAST(:cutoff AS date)
), reference_keys AS MATERIALIZED (
 SELECT DISTINCT venue_code,track_code,distance_meters FROM requests
), winners AS MATERIALIZED (
 SELECT race_id,AVG(elapsed_seconds)::double precision AS winner_seconds,
  AVG(last_three_furlongs_seconds)::double precision AS winner_closing
 FROM jv_data.race_results WHERE race_date>=DATE '2010-01-01' AND race_date<CAST(:cutoff AS date)
  AND finish_position=1 AND is_starter AND is_finisher AND is_final_result AND is_complete_result
 GROUP BY race_id HAVING AVG(elapsed_seconds) IS NOT NULL
), reference_history AS MATERIALIZED (
 SELECT p.race_id,p.race_date,p.venue_code,p.track_code,p.distance_meters,
  CASE WHEN LEFT(p.track_code,1)='1' THEN p.turf_condition_code ELSE p.dirt_condition_code END AS ground,
  w.winner_seconds,w.winner_closing,
  p.first_three_furlongs_seconds::double precision AS pace_seconds
 FROM jv_data.races p JOIN reference_keys k ON k.venue_code=p.venue_code
  AND k.track_code=p.track_code AND k.distance_meters=p.distance_meters
 JOIN winners w ON w.race_id=p.race_id
 WHERE p.race_date>=DATE '2010-01-01' AND p.race_date<CAST(:cutoff AS date)
)
SELECT q.race_id,stats.* FROM requests q LEFT JOIN LATERAL (
 WITH prior AS MATERIALIZED (
  SELECT p.* FROM reference_history p
  WHERE p.venue_code=q.venue_code AND p.track_code=q.track_code AND p.distance_meters=q.distance_meters
   AND p.race_date<q.race_date AND p.race_date>=DATE '2010-01-01'
  ORDER BY p.race_date DESC,p.race_id DESC LIMIT 400
 ), counted AS (
  SELECT *,COUNT(*) FILTER (WHERE ground=q.ground) OVER () AS matching FROM prior
 ), selected AS (
  SELECT * FROM counted WHERE matching<20 OR ground=q.ground
 )
 SELECT COUNT(winner_seconds)::double precision AS clock_count,
  CASE WHEN COUNT(winner_seconds)>=20 THEN AVG(winner_seconds) END AS clock_mean,
  CASE WHEN COUNT(winner_seconds)>=20 THEN GREATEST(0.5,STDDEV_SAMP(winner_seconds)) END AS clock_std,
  COUNT(winner_closing)::double precision AS closing_count,
  CASE WHEN COUNT(winner_closing)>=20 THEN AVG(winner_closing) END AS closing_mean,
  CASE WHEN COUNT(winner_closing)>=20 THEN GREATEST(0.2,STDDEV_SAMP(winner_closing)) END AS closing_std,
  COUNT(pace_seconds)::double precision AS pace_count,
  CASE WHEN COUNT(pace_seconds)>=20 THEN AVG(pace_seconds) END AS pace_mean,
  CASE WHEN COUNT(pace_seconds)>=20 THEN GREATEST(0.3,STDDEV_SAMP(pace_seconds)) END AS pace_std
 FROM selected
) stats ON TRUE ORDER BY q.race_id"""


def normalize_sessions(
    sessions: Sequence[Mapping[str, Any]], reference: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    keys = ("training_type", "training_center_code", "course_code")
    for row in reference:
        groups[tuple(str(row.get(key) or "") for key in keys)].append(row)
    result = []
    for session in sessions:
        current = day(session["training_date"])
        prior = [
            row
            for row in groups[tuple(str(session.get(key) or "") for key in keys)]
            if 0 < (current - day(row["training_date"])).days <= 365
        ]
        copy = dict(session)
        copy.update({key: str(session.get(key) or "") for key in keys})
        for suffix, field, target, bounds in (
            ("4", "four_furlongs_seconds", "normalized_4f", (35, 80)),
            ("1", "lap_200_0_seconds", "normalized_last1f", (8, 25)),
        ):
            value = number(session.get(field))
            copy[target] = (
                training_z(
                    value,
                    *(sum(number(row.get(f"{name}{suffix}"), 0) for row in prior) for name in ("n", "sum", "sq")),
                )
                if bounds[0] <= value <= bounds[1]
                else float("nan")
            )
        result.append(copy)
    return result


async def augment(
    database: JvDataDatabase,
    cards: list[dict[str, Any]],
    rows: list[dict[str, float]],
    history: Mapping[str, Sequence[dict[str, Any]]],
    sessions: Mapping[str, Sequence[dict[str, Any]]],
) -> dict[str, Any]:
    cutoff = str(cards[0]["race_date"])
    all_ids = sorted({str(cards[0]["race_id"]), *(str(run["race_id"]) for runs in history.values() for run in runs)})
    classes = await fetch_rows(database, CLASS_FOR_RACES_SQL, {"race_ids": all_ids}, statement_timeout_seconds=60)
    codes: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in classes:
        codes[row["race_id"]][row["age_key"]] = row["condition_code"]
    recent_ids = sorted(
        {str(run["race_id"]) for runs in history.values() for run in [r for r in runs if valid_run(r)][:20]}
    )
    contexts = (
        await fetch_rows(
            database,
            CLOCK_REFERENCE_SQL,
            {"past_races": recent_ids, "cutoff": cutoff},
            statement_timeout_seconds=60,
            disable_jit=True,
        )
        if recent_ids
        else []
    )
    contexts = {
        row["race_id"]: {name: number(value) for name, value in row.items() if name != "race_id"} for row in contexts
    }
    reference = await fetch_rows(database, TRAINING_POPULATION_SQL, {"cutoff": cutoff}, statement_timeout_seconds=60)
    for card, row in zip(cards, rows, strict=True):
        card = {**card, "official_class_rank": official_class(codes.get(str(card["race_id"])), card.get("horse_age"))}
        past = []
        for run in history[str(card["horse_id"])]:
            copy = {
                **run,
                "official_class_rank": official_class(codes.get(str(run["race_id"])), run.get("feature_horse_age")),
            }
            context = contexts.get(str(run["race_id"]))
            if context:
                copy.update(clock_values(copy, context))
            past.append(copy)
        training = normalize_sessions(sessions.get(str(card["horse_id"]), []), reference)
        row.update(runner_extensions(card, past, training))
    add_field_extensions(rows)
    return {
        "class_conflicts": sum(bool(row["conflict"]) for row in classes),
        "clock_history_races": len(contexts),
        "training_population_days": len(reference),
        "extended_observations": "strict_prior_date; stored card publication version unverified",
    }
