"""調教を全頭取得し、同一セッションの時計と未観察事項を保持する。"""

import asyncio

import pytest

from jvdata_bridge_mcp.tools.training import entry_detail
from jvdata_bridge_mcp.tools.training._normalization import relative_training_scores


def test_cohort_comparison_does_not_mix_centers() -> None:
    rows = [
        dict(
            training_type="坂路",
            training_center_code="miho",
            course_code="1",
            quality_status="ok",
            four_furlongs_seconds=60,
        ),
        dict(
            training_type="坂路",
            training_center_code="ritto",
            course_code="1",
            quality_status="ok",
            four_furlongs_seconds=40,
        ),
    ]
    assert relative_training_scores(rows) == (5.0, 5.0, 1)


@pytest.mark.parametrize(
    ("history_limit", "returned_count", "complete"), [(0, 270, True), (3, 54, False), (20, 270, True)]
)
def test_bulk_training_keeps_over_200_sessions_and_same_run_best(
    monkeypatch: pytest.MonkeyPatch, history_limit: int, returned_count: int, complete: bool
) -> None:
    async def fetch(*args: object, **kwargs: object) -> list[dict[str, object]]:
        return [
            dict(
                horse_number=n,
                horse_id=f"h{n}",
                horse_name=f"H{n}",
                training_date=f"2026-09-{d:02d}",
                training_type="坂路",
                training_center_code="miho",
                quality_status="ok",
                four_furlongs_seconds=50 + d,
                lap_200_0_seconds=18 - d / 10,
            )
            for n in range(1, 19)
            for d in range(20, 5, -1)
        ]

    monkeypatch.setattr(entry_detail, "fetch_rows", fetch)
    output = asyncio.run(
        entry_detail.build_training_entry_detail_tool(object())(
            race_code="2026092206040705", all_horses=True, history_limit=history_limit
        )
    )
    assert sum(item.session_count for item in output.items) == 270
    assert output.session_count == 270
    assert output.comparable_session_count == sum(item.comparable_session_count for item in output.items) == 270
    assert sum(len(item.recent_history) for item in output.items) == returned_count
    assert output.all_history_returned is complete
    assert all(item.all_history_returned is complete for item in output.items)
    first = output.items[0]
    assert first.best_times["f4"]["session"]["last_1f"] == 17.4
    assert first.best_times["last_1f"]["session"]["time_4f"] == 70
    assert first.fitness_inference is None
