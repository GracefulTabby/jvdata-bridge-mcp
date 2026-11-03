"""レースコードの形式と開催日を検証する。時刻の境界は対象レースから別に決める。"""

from __future__ import annotations

import re
from datetime import date


def race_date_from_code(race_code: str) -> date:
    """16桁のコードを検証し、先頭8桁の開催日を返す。発走時刻は含まれない。"""

    if re.fullmatch(r"[0-9]{16}", race_code) is None:
        raise ValueError("race_code must contain exactly 16 ASCII digits")
    try:
        return date(int(race_code[:4]), int(race_code[4:6]), int(race_code[6:8]))
    except ValueError as exc:
        raise ValueError("race_code must contain a valid race date") from exc
