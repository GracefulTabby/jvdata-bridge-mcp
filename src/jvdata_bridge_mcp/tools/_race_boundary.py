"""対象レースと現在時刻から、利用できる実績の範囲を自動で決める。"""

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._observation_scope import JST, now_jst
from jvdata_bridge_mcp.tools._race_date import race_date_from_code
from jvdata_bridge_mcp.tools._runtime import fetch_rows

RaceCode = Annotated[str, Field(description="予想対象のレースコード。省略は現在までの収録済み成績")]


class RaceBoundary(BaseModel):
    race_code: str | None = None
    race_date: str | None = None
    scheduled_start_at: str | None = None
    analysis_at: str
    history_before_at: str
    timezone: str = "Asia/Tokyo"
    policy: str = "completed_results_before_target_and_now; same_day_requires_known_start; no_historical_replay"

    def parameters(self) -> dict[str, object]:
        cutoff = datetime.fromisoformat(self.history_before_at).astimezone(JST)
        return {
            "race_code": self.race_code or "",
            "boundary_date": cutoff.date().isoformat(),
            "boundary_local": cutoff.replace(tzinfo=None).isoformat(),
        }


async def resolve_race_boundary(database: JvDataDatabase, race_code: str = "") -> RaceBoundary:
    """過去時点の再現はせず、未発走の対象レースを確認する。"""

    now = now_jst().astimezone(JST)
    if not race_code:
        return RaceBoundary(analysis_at=now.isoformat(), history_before_at=now.isoformat())
    target = race_date_from_code(race_code)
    if target < now.date():
        raise ValueError("forecast requires an upcoming race; historical replay is not supported")
    rows = await fetch_rows(
        database,
        "SELECT race_date,scheduled_start_at FROM jv_data.races WHERE race_id=:race_code",
        {"race_code": race_code},
    )
    if not rows or str(rows[0].get("race_date")) != target.isoformat():
        raise ValueError("target race is unavailable or its date is inconsistent")
    raw_start = rows[0].get("scheduled_start_at")
    start = datetime.fromisoformat(str(raw_start)) if raw_start else None
    if start is not None:
        start = start.replace(tzinfo=JST) if start.tzinfo is None else start.astimezone(JST)
        if start.date() != target:
            raise ValueError("target race date and scheduled start are inconsistent")
        if start <= now:
            raise ValueError("forecast requires an upcoming race")
    elif target == now.date():
        raise ValueError("today forecast requires a confirmed scheduled start")
    return RaceBoundary(
        race_code=race_code,
        race_date=target.isoformat(),
        scheduled_start_at=start.isoformat() if start else None,
        analysis_at=now.isoformat(),
        history_before_at=min(now, start).isoformat() if start else now.isoformat(),
    )


def before_boundary_sql(alias: str) -> str:
    """日付で索引を絞り、同日の前後関係だけを発走時刻で検証する。"""

    return f"""(
        {alias}.race_id <> :race_code
        AND {alias}.race_date <= CAST(:boundary_date AS date)
        AND ({alias}.race_date < CAST(:boundary_date AS date) OR EXISTS (
            SELECT 1 FROM jv_data.races boundary_previous
            WHERE boundary_previous.race_id={alias}.race_id
              AND boundary_previous.scheduled_start_at < CAST(:boundary_local AS timestamp)
        ))
    )"""


def before_target_race_sql(alias: str, target_date: str) -> str:
    """レース単位のSQL用。利用者が時点を渡す必要はない。"""

    day = f"LEAST({target_date}, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Tokyo')::date)"
    return f"""(
        {alias}.race_id <> :race_code
        AND {alias}.race_date <= {day}
        AND ({alias}.race_date < {day} OR EXISTS (
            SELECT 1 FROM jv_data.races boundary_previous
            WHERE boundary_previous.race_id={alias}.race_id
              AND boundary_previous.scheduled_start_at < LEAST(
                  (SELECT scheduled_start_at FROM jv_data.races WHERE race_id=:race_code),
                  CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Tokyo'
              )
              AND ({target_date} > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Tokyo')::date
                   OR (SELECT scheduled_start_at FROM jv_data.races WHERE race_id=:race_code) IS NOT NULL)
        ))
    )"""


def training_before_target_sql(alias: str, target_date: str) -> str:
    """同日の調教は時刻が分かる場合だけ含める。日付だけで朝とは推測しない。"""

    day = f"LEAST({target_date}, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Tokyo')::date)"
    return f"""(
        {alias}.training_date <= {day}
        AND ({alias}.training_date < {day} OR (
            {alias}.training_time IS NOT NULL
            AND ({target_date} > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Tokyo')::date
                 OR (SELECT scheduled_start_at FROM jv_data.races WHERE race_id=:race_code) IS NOT NULL)
            AND {alias}.training_date + CAST({alias}.training_time AS time) < LEAST(
                (SELECT scheduled_start_at FROM jv_data.races WHERE race_id=:race_code),
                CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Tokyo'
            )
        ))
    )"""


def period_dates(reference: date, period: str) -> tuple[str, str]:
    """年度は対象レースから決める。時刻の上限は共通境界で別に適用する。"""

    if period == "career":
        return "1800-01-01", "9999-12-31"
    if period == "current_year":
        return date(reference.year, 1, 1).isoformat(), date(reference.year + 1, 1, 1).isoformat()
    if period == "previous_year":
        return date(reference.year - 1, 1, 1).isoformat(), date(reference.year, 1, 1).isoformat()
    raise ValueError("period must be career, current_year or previous_year")
