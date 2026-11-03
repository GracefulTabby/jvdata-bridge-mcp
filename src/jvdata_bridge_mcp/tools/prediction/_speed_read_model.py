"""事前計算済み速度指標の対象馬被覆を確認する。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._observation_scope import now_jst
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.prediction._indicator_sql import INDICATOR_MODEL_VERSION

_COVERAGE_SQL = """
WITH target AS (
    SELECT
        r.race_date,
        r.race_date AS history_before_date,
        r.track_code, r.venue_code, r.distance_meters
    FROM jv_data.races AS r
    WHERE r.race_id = :race_code
)
SELECT
    t.history_before_date,
    t.track_code, t.venue_code, t.distance_meters,
    COUNT(DISTINCT c.horse_id) AS target_horses,
    COUNT(DISTINCT stored.horse_id) FILTER (
        WHERE stored.speed_observation_count BETWEEN 0 AND 5
          AND stored.speed_quality_raw BETWEEN 0.0 AND 1.0
          AND stored.speed_quality_coverage BETWEEN 0.0 AND 1.0
          AND ABS(stored.speed_quality_coverage - stored.speed_observation_count / 5.0) < 0.000000001
          AND (
              (stored.speed_observation_count = 0 AND stored.speed_baseline_level IS NULL)
              OR (stored.speed_observation_count > 0 AND stored.speed_baseline_level IS NOT NULL)
          )
    ) AS available_horses
FROM target AS t
LEFT JOIN jv_mart.race_cards AS c ON c.race_id = :race_code AND c.horse_number BETWEEN 1 AND 18
LEFT JOIN jv_mart.horse_speed_quality AS stored
  ON stored.race_id = :race_code
 AND stored.race_date = t.race_date
 AND stored.horse_id = c.horse_id
 -- Exact race/date/model identifies the documented prior-date aggregation.
 AND stored.model_version = :speed_model_version
GROUP BY t.history_before_date, t.track_code, t.venue_code, t.distance_meters
"""


class SpeedQualityStatus(BaseModel):
    race_code: str
    analysis_at: str
    model_version: str
    history_before_date: str | None = None
    track_code: str | None = None
    venue_code: str | None = None
    distance_meters: int | None = None
    declared_entries: int = 0
    available_entries: int = 0
    missing_or_invalid_entries: int = 0
    status: Literal["ready", "race_unavailable", "no_declared_entries", "missing_or_invalid_speed_rows"]
    ready: bool
    observation_scope: str = "exact_target_date_and_model; prior_date_aggregation"
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Readiness checks required stored rows, not prediction accuracy or the upstream update schedule.",
            "Declared entries include horses whose current withdrawal status is reported separately.",
        ]
    )


async def get_speed_quality_status(
    database: JvDataDatabase, race_code: str, *, analysis_at: str | None = None
) -> SpeedQualityStatus:
    """不足を例外にせず返す。独立した馬評価は事前計算不足でも継続できる。"""

    rows = await fetch_rows(
        database,
        _COVERAGE_SQL,
        {"race_code": race_code, "speed_model_version": INDICATOR_MODEL_VERSION},
        statement_timeout_seconds=10,
    )
    row = rows[0] if rows else {}
    total = int(row.get("target_horses") or 0)
    available = int(row.get("available_horses") or 0)
    status = (
        "race_unavailable"
        if not rows
        else "no_declared_entries"
        if not total
        else ("ready" if available == total else "missing_or_invalid_speed_rows")
    )
    return SpeedQualityStatus(
        race_code=race_code,
        analysis_at=analysis_at or now_jst().isoformat(),
        model_version=INDICATOR_MODEL_VERSION,
        history_before_date=str(row["history_before_date"]) if row.get("history_before_date") else None,
        track_code=row.get("track_code"),
        venue_code=row.get("venue_code"),
        distance_meters=row.get("distance_meters"),
        declared_entries=total,
        available_entries=available,
        missing_or_invalid_entries=max(0, total - available),
        status=status,
        ready=status == "ready",
    )


async def ensure_speed_quality_available(
    database: JvDataDatabase,
    race_code: str,
) -> None:
    """対象の全馬に同じモデル版・履歴排他日の速度行があることを確認する。"""

    status = await get_speed_quality_status(database, race_code)
    if not status.ready:
        raise ValueError(
            f"precomputed speed_quality missing or invalid for race_code={race_code}, "
            f"history_before_date={status.history_before_date}, model_version={INDICATOR_MODEL_VERSION}: "
            f"missing {status.missing_or_invalid_entries} of {status.declared_entries} declared entries; "
            f"status={status.status}. Use prediction_model_status for prerequisites; "
            "race_forecast_context remains available for independent horse evaluation."
        )
