"""JRA一次資料で確認できたコース変更境界。未知の変更日は推測しない。"""

from __future__ import annotations


def course_configuration(venue: str, track: str, target_date: str) -> dict[str, object]:
    if venue == "07" and track[:1] in {"1", "2"}:
        boundary = "2012-03-03"
        return {
            "status": "confirmed_rebuild_boundary",
            "history_start_date": boundary if target_date >= boundary else None,
            "configuration": "chukyo_2012_rebuild" if target_date >= boundary else "chukyo_before_2012_rebuild",
            "source_urls": [
                "https://www.jra.go.jp/facilities/race/chukyo/course/index.html",
                "https://www.jra.go.jp/keiba/program/pdf/h24-kaisainittei.pdf",
            ],
            "boundary_date": boundary,
            "completeness": "known_boundary_only; later_changes_not_exhaustively_verified",
        }
    if venue == "09" and track == "18":
        boundary = "2006-12-02"
        return {
            "status": "confirmed_outer_course_opening",
            "history_start_date": boundary if target_date >= boundary else None,
            "configuration": "hanshin_outer_2006",
            "boundary_date": boundary,
            "source_urls": [
                "https://jra.jp/company/about/outline/history/",
                "https://jra.jp/datafile/seiseki/report/2006/3han.pdf",
            ],
            "completeness": "outer_course_only; other_layout_changes_unknown",
        }
    return {
        "status": "configuration_boundary_unverified",
        "history_start_date": None,
        "configuration": "stored_track_code_only",
        "source_urls": [],
        "completeness": "not_exhaustively_verified",
    }
