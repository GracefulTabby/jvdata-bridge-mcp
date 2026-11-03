"""人物成績の期間・走路・場所を共通化する。"""

from jvdata_bridge_mcp.tools._race_boundary import before_boundary_sql

VENUES = dict(
    zip(
        ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"),
        (f"{n:02d}" for n in range(1, 11)),
        strict=True,
    )
)
SURFACES = {"芝": "turf", "ダート": "dirt", "障害": "jump", "turf": "turf", "dirt": "dirt", "jump": "jump", "": ""}
PERIODS = {"career": 3, "current_year": 1, "previous_year": 2}


def person_period(period: str) -> int:
    if period not in PERIODS:
        raise ValueError("period must be career, current_year or previous_year")
    return PERIODS[period]


def surface_code(value: str) -> str:
    try:
        return SURFACES[value.strip()]
    except KeyError as exc:
        raise ValueError("track_type must be 芝, ダート, 障害, turf, dirt or jump") from exc


def venue_code(value: str) -> str:
    value = value.strip().removesuffix("競馬場")
    if not value:
        return ""
    result = VENUES.get(value, value)
    if result not in VENUES.values():
        raise ValueError("racecourse_name must be a JRA venue name or code 01..10")
    return result


def person_history_sql(person_type: str) -> str:
    """実際の出走明細から交差条件を集計する（同じ走を一度だけ数える）。"""

    if person_type not in {"jockey", "trainer"}:
        raise ValueError("unsupported person type")
    return f"""
WITH runs AS MATERIALIZED (
    SELECT DISTINCT ON (r.race_id, r.horse_id) r.*,
        c.{person_type}_name AS person_name,
        CASE WHEN r.track_name LIKE '%障害%' THEN 'jump'
             WHEN r.track_name LIKE '%芝%' THEN 'turf' ELSE 'dirt' END AS surface
    FROM jv_mart.race_cards c
    JOIN jv_mart.race_results r ON r.race_id = c.race_id AND r.horse_id = c.horse_id
    WHERE c.{person_type}_id = :person_id
      AND r.race_date >= CAST(:start_date AS date) AND r.race_date < CAST(:end_date AS date)
      AND {before_boundary_sql("r")}
      AND (r.is_final_result IS TRUE OR (:include_provisional AND r.result_status_code='provisional_complete'))
      AND r.is_complete_result IS TRUE AND r.is_starter IS TRUE
      AND r.venue_code BETWEEN '01' AND '10'
      AND (:venue = '' OR r.venue_code = :venue)
      AND (:distance_min = 0 OR r.distance_meters >= :distance_min)
      AND (:distance_max = 0 OR r.distance_meters <= :distance_max)
      AND (:is_graded = FALSE OR r.grade_code IN ('A','B','C','F','G','H'))
    ORDER BY r.race_id, r.horse_id
)
SELECT :person_id AS {person_type}_code, MAX(person_name) AS {person_type}_name,
    :venue AS racecourse_code, :surface AS track_type, NULL::text AS distance_band_code,
    COUNT(*) FILTER (WHERE is_final_result IS FALSE) AS provisional_starts,
    COUNT(*) AS starts, COUNT(*) FILTER (WHERE finish_position = 1) AS wins,
    COUNT(*) FILTER (WHERE finish_position BETWEEN 1 AND 3) AS top3_finishes,
    SUM(prize_money_yen) FILTER (WHERE surface <> 'jump') AS flat_prize_money_yen,
    MIN(race_date) AS evidence_start_date, MAX(race_date) AS evidence_end_date,
    COUNT(*) FILTER (WHERE is_finisher IS NOT TRUE) AS non_finishers
FROM runs WHERE (:surface = '' OR surface = :surface OR (:surface = 'flat' AND surface <> 'jump'))
"""
