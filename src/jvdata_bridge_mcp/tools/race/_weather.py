"""WFの差分通知から、天候・芝・ダートをそれぞれ復元する。"""

from jvdata_bridge_mcp.tools._source_timing import source_timing

WEATHER_EVENTS_SQL = """
WITH target AS MATERIALIZED (
    SELECT race_id,race_date,scheduled_start_at,venue_code,venue_name,track_code,track_name
    FROM jv_data.races WHERE race_id=:race_code
), observation_day AS MATERIALIZED (
    SELECT MAX(w.race_date) AS race_date
    FROM jv_mart.weather_track w CROSS JOIN target t
    WHERE w.venue_code=t.venue_code
      AND w.race_date=CAST(:today AS date) AND w.race_date<=t.race_date
      AND w.announced_at<=CAST(:analysis_local AS timestamp)
      AND (t.race_date>CAST(:today AS date) OR CAST(:analysis_local AS timestamp)<t.scheduled_start_at)
)
SELECT t.race_id AS race_code,t.race_date AS target_race_date,
       t.venue_code AS racecourse_code,t.venue_name AS racecourse_name,t.track_code,t.track_name,
       w.race_date AS observation_date,w.announced_at,w.change_type_code,
       w.current_weather_code,w.current_weather_name,
       w.current_turf_condition_code,w.current_turf_condition_name,
       w.current_dirt_condition_code,w.current_dirt_condition_name,
       w.artifact_hash,w.source_sequence
FROM target t LEFT JOIN observation_day d ON TRUE
LEFT JOIN jv_mart.weather_track w ON w.venue_code=t.venue_code AND w.race_date=d.race_date
  AND w.announced_at<=CAST(:analysis_local AS timestamp)
  AND (t.scheduled_start_at IS NULL OR w.announced_at<t.scheduled_start_at)
ORDER BY w.announced_at NULLS FIRST,w.source_sequence NULLS FIRST,w.artifact_hash
"""


def _event_order(row: dict[str, object]) -> tuple[str, str, int, str]:
    return (
        str(row.get("observation_date") or ""),
        str(row.get("announced_at") or ""),
        int(row.get("source_sequence") or 0),
        str(row.get("artifact_hash") or ""),
    )


def reconstruct_weather(rows: list[dict[str, object]], *, analysis_at: str | None = None) -> dict[str, object]:
    """0/空欄はその項目の更新ではない。過去日の値は当日へ持ち越さない。"""

    fields = {
        "weather": {str(n) for n in range(1, 7)},
        "turf_condition": {"1", "2", "3", "4"},
        "dirt_condition": {"1", "2", "3", "4"},
    }
    result: dict[str, object] = {}
    provenance: dict[str, object] = {}
    day = None
    for row in sorted(rows, key=_event_order):
        if row.get("observation_date") != day:
            day = row.get("observation_date")
            result, provenance = {}, {}
        for field, valid in fields.items():
            code = str(row.get(f"current_{field}_code") or "").strip()
            if code not in valid:
                continue
            result[f"current_{field}_code"] = code
            result[f"current_{field}_name"] = row.get(f"current_{field}_name")
            provenance[field] = {k: row.get(k) for k in ("announced_at", "artifact_hash", "source_sequence")}
            if analysis_at:
                provenance[field]["source_timing"] = source_timing(
                    analysis_at, published_at=row.get("announced_at")
                ).model_dump()
        if row.get("announced_at"):
            result["announced_at"] = row["announced_at"]
    for field in fields:
        result.setdefault(f"current_{field}_code", None)
        result.setdefault(f"current_{field}_name", None)
    result.update(
        observation_date=day,
        field_observations=provenance,
        observation_status="complete" if len(provenance) == 3 else "partial" if provenance else "unavailable",
        source="jv_mart.weather_track; WF events reconstructed per field",
    )
    return result
