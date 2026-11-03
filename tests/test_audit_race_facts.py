"""RA年齢条件と履歴の意味を検証する。"""

from jvdata_bridge_mcp.tools.race._class_facts import CLASS_NAMES, CLASS_RANKS, class_join_sql
from jvdata_bridge_mcp.tools.race.condition_changes import _SQL as CHANGES
from jvdata_bridge_mcp.tools.race.opponent_class import _items_from_rows
from jvdata_bridge_mcp.tools.race.recent_form import RaceRecentFormItem


def test_grade_is_not_a_substitute_for_jra_age_condition() -> None:
    sql = class_join_sql("r", "cl", "h.horse_age")
    assert "競走条件コード 5歳以上条件" in sql
    assert "distinct_conditions = 1" in sql
    assert "r.grade_name" in sql and "NULLIF(r.grade_name" not in sql
    assert CLASS_NAMES["005"] == "1勝クラス"
    assert CLASS_RANKS["999"] == 60
    assert "001" not in CLASS_RANKS


def test_every_recent_run_can_expose_training_independent_facts() -> None:
    assert {
        "track_condition",
        "horse_age",
        "assigned_weight_kg",
        "jockey_id",
        "abnormality_code",
        "is_finisher",
        "margin_code",
        "winner_to_second_margin_seconds",
        "race_laps",
        "fact_id",
    } <= set(RaceRecentFormItem.model_fields)
    assert "all_stored_runs_searched" in CHANGES and "outside_jra_history" in CHANGES
    assert "ch.race_id <> :race_code" in CHANGES


def test_direct_meeting_does_not_require_opponent_in_top_three() -> None:
    row = dict(
        horse_number=1,
        horse_id="a",
        horse_name="A",
        run_number=1,
        past_race_code="2026010101010101",
        past_race_date="2026-01-01",
        class_name="1勝クラス",
        opponent_horse_id="b",
        opponent_horse_name="B",
        opponent_finish_position=5,
        in_current_field=True,
    )
    item = _items_from_rows([row])[0]
    assert not item.top_three_opponents
    assert item.current_field_opponents[0]["finish_position"] == 5
    assert item.fact_id == "jra-run:2026010101010101:a"


def test_odd_distance_laps_and_jump_do_not_invent_600m_opening() -> None:
    from jvdata_bridge_mcp.tools.race._laps import lap_segment_lengths, opening_segment_meters

    assert lap_segment_lengths(1700, "24") == [100] + [200] * 8
    assert opening_segment_meters(1700, "24", 3) == 500
    assert opening_segment_meters(1700, "24", 4) == 700
    assert opening_segment_meters(1800, "24", 3) == 600
    assert opening_segment_meters(2860, "54", 3) is None
    assert lap_segment_lengths(1000, None) is None
