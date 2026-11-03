"""ペース関連ツールの対象レース前の履歴境界を検証する。"""

from __future__ import annotations

from jvdata_bridge_mcp.tools.pace.course_profile import _SQL as COURSE_PROFILE_SQL
from jvdata_bridge_mcp.tools.pace.running_styles import _SQL as RUNNING_STYLES_SQL
from jvdata_bridge_mcp.tools.race.similar import _SQL as SIMILAR_RACES_SQL


def test_running_styles_use_only_final_history_before_target_date() -> None:
    """脚質とコーナー位置は対象レース自身の結果から作らない。"""

    assert "LEFT JOIN LATERAL" in RUNNING_STYLES_SQL
    assert "h.race_id <> :race_code" in RUNNING_STYLES_SQL
    assert "h.is_final_result IS TRUE" in RUNNING_STYLES_SQL
    assert "h.race_id = c.race_id" not in RUNNING_STYLES_SQL


def test_course_profile_excludes_the_target_race_and_future_races() -> None:
    """コースペース事実は基準レースより前のレースだけを参照する。"""

    assert "r.race_id <> :race_code" in COURSE_PROFILE_SQL
    assert "CURRENT_DATE" not in COURSE_PROFILE_SQL
    assert "race_results" not in COURSE_PROFILE_SQL


def test_similar_races_are_bounded_to_history_before_the_target() -> None:
    """類似レース候補は対象レースより前に限定し、結果表を参照しない。"""

    assert "r.race_id <> :race_code" in SIMILAR_RACES_SQL
    assert "CURRENT_DATE" not in SIMILAR_RACES_SQL
    assert "race_results" not in SIMILAR_RACES_SQL
