"""対象レースと現在時刻による、発表観測の検索範囲。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from jvdata_bridge_mcp.tools._race_date import race_date_from_code

JST = ZoneInfo("Asia/Tokyo")


def now_jst() -> datetime:
    return datetime.now(JST)


def observation_parameters(race_code: str) -> dict[str, object]:
    """取り込み時刻でなく観測の発表時刻に適用する境界を返す。"""

    target = race_date_from_code(race_code)
    now = now_jst()
    if target < now.date():
        raise ValueError("forecast requires an upcoming race; historical replay is not supported")
    return {
        "race_code": race_code,
        "target_race_date": target.isoformat(),
        "observation_scope": "live",
        "analysis_at": now.isoformat(),
        "analysis_local": now.replace(tzinfo=None).isoformat(),
        "today": now.date().isoformat(),
    }
