"""走路・競走種別・クラスの条件比較を区別する。"""

from jvdata_bridge_mcp.tools.race.condition_changes import _build_condition_changes, _make_last_start


def comparison(current: str, previous: str) -> object:
    row = dict(
        current_track=current,
        previous_track_name=previous,
        previous_race_code="past",
        horse_id="h",
        current_class_name="1勝クラス",
        previous_class_name="オープン",
        current_distance=1800,
        previous_distance=2860,
        current_assigned_weight=57,
        previous_assigned_weight=60,
    )
    return _build_condition_changes(row, _make_last_start(row))


def test_inner_outer_or_direction_change_is_not_turf_dirt_change() -> None:
    change = comparison("平地 芝 右回り 内回り", "平地 芝 左回り 外回り")
    assert change.surface.current == change.surface.previous == "芝"
    assert change.surface.changed is False
    assert change.course_direction.changed is True
    assert change.course_layout.current == "inner" and change.course_layout.previous == "outer"
    assert change.track_configuration.changed is True
    assert change.race_class.comparable is True


def test_jump_to_flat_has_physical_deltas_without_class_ability_comparison() -> None:
    change = comparison("平地 芝 右回り", "障害 芝 右回り")
    assert change.race_domain.current == "flat" and change.race_domain.previous == "jump"
    assert change.surface.changed is False
    assert change.race_class.change == "incomparable"
    assert change.race_class.rank_delta is None
    assert change.assigned_weight_change == -3 and change.assigned_weight_comparable is False
    assert change.distance.delta == -1060 and change.distance_comparable is False
    assert "not_verified_rest" in change.interval_semantics


def test_unknown_layout_and_no_history_are_unknown() -> None:
    change = comparison("平地 芝 右回り", "平地 芝 左回り")
    assert change.course_layout.changed is None
    missing = _build_condition_changes(dict(current_track="平地 ダート 右回り", current_class_name="未勝利"), None)
    assert missing.race_class.change == "unknown"
    assert missing.race_class.comparable is None
    assert missing.surface.changed is None
