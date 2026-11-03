"""実際の発表日時から経過時間を表示し、取得時刻や取り込み遅延は推測しない。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from jvdata_bridge_mcp.tools._observation_scope import JST


class SourceTiming(BaseModel):
    analysis_at: str
    published_at: str | None = None
    age_seconds: float | None = Field(default=None, description="発表から分析時刻までの秒数。取得時刻から推測しない")
    age_status: Literal["missing", "time_unknown", "age_known", "recent", "aged", "future_timestamp"]
    advisory_max_age_seconds: int | None = Field(
        default=None, description="経過時間の表示用目安。観測の除外条件ではない"
    )
    upstream_latest_verified: bool = Field(
        default=False, description="保存済み最新観測だけでは配信元の最新性を保証しない"
    )


def source_datetime(value: object) -> datetime | None:
    """時刻のない値を補完せず、timezoneなしのJV発表日時はJSTとして読む。"""

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and ("T" in value or " " in value):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed.astimezone(JST)


def source_timing(
    analysis_at: str | datetime,
    *,
    published_at: object = None,
    observed: bool = True,
    advisory_max_age_seconds: int | None = None,
) -> SourceTiming:
    """古い観測も残し、発表日時不明と未収録を区別する。"""

    now = source_datetime(analysis_at)
    if now is None:
        raise ValueError("analysis_at requires a timestamp")
    published = source_datetime(published_at)
    age = (now - published).total_seconds() if published else None
    status = "missing" if not observed else "time_unknown" if age is None else "age_known"
    if age is not None and age < 0:
        status = "future_timestamp"
    elif age is not None and advisory_max_age_seconds is not None:
        status = "recent" if age <= advisory_max_age_seconds else "aged"
    return SourceTiming(
        analysis_at=now.isoformat(),
        published_at=published.isoformat() if published else None,
        age_seconds=age,
        age_status=status,
        advisory_max_age_seconds=advisory_max_age_seconds,
    )
