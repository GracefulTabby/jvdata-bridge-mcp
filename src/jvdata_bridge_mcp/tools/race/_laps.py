"""平地の距離から、JRAの先頭端数を含むラップ区間長を示す。"""


def lap_segment_lengths(distance: int | None, track_code: str | None) -> list[int] | None:
    if not distance or not track_code or track_code[:1] not in {"1", "2"}:
        return None
    full, remainder = divmod(distance, 200)
    return ([remainder] if remainder else []) + [200] * full


def opening_segment_meters(distance: int | None, track_code: str | None, segments: int) -> int | None:
    lengths = lap_segment_lengths(distance, track_code)
    return sum(lengths[:segments]) if lengths and len(lengths) >= segments else None


def lap_window_comparison(distance: int | None, track_code: str | None, segments: int) -> dict[str, object]:
    """冒頭の実区間長と終盤600/800mの重複・未計測区間を記述する。"""

    opening = opening_segment_meters(distance, track_code, segments)
    closing = segments * 200 if opening is not None and distance and distance >= segments * 200 else None
    if opening is None or closing is None or distance is None:
        return {
            "status": "unavailable",
            "opening_meters": opening,
            "closing_meters": closing,
            "overlap_meters": None,
            "uncovered_middle_meters": None,
            "equal_length_windows": None,
        }
    return {
        "status": "available",
        "opening_meters": opening,
        "closing_meters": closing,
        "overlap_meters": max(0, opening + closing - distance),
        "uncovered_middle_meters": max(0, distance - opening - closing),
        "equal_length_windows": opening == closing,
        "semantics": "recorded_opening_segments_vs_official_closing_600_or_800m; not_whole_race_pace",
    }
