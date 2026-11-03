"""発走前の変更通知と情報の古さを、結果や取り込み時刻で補完しない。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from jvdata_bridge_mcp.tools._source_timing import source_timing
from jvdata_bridge_mcp.tools.prediction import _speed_read_model
from jvdata_bridge_mcp.tools.prediction import status as model_status
from jvdata_bridge_mcp.tools.race._entry_changes import current_entry_observations

NOW = "2024-01-06T14:30:00+09:00"
START = "2024-01-06T15:00:00+09:00"
ENTRIES = [
    {
        "horse_number": 1,
        "horse_id": "horse-1",
        "horse_name": "Horse 1",
        "jockey_id": "original",
        "assigned_weight_kg": 55.0,
    },
    {"horse_number": 2, "horse_id": "horse-2", "horse_name": "Horse 2", "abnormality_code": "1"},
]


def test_announcement_age_does_not_invent_retrieval_or_ingestion_times() -> None:
    timing = source_timing(NOW, published_at="2024-01-06T13:00:00", advisory_max_age_seconds=300)
    assert timing.age_seconds == 5400
    assert not {"retrieved_at", "retrieval_age_seconds", "ingestion_delay_seconds"} & timing.model_dump().keys()
    assert timing.age_status == "aged"
    assert not timing.upstream_latest_verified


def test_missing_publication_time_is_not_invented_from_analysis_time() -> None:
    timing = source_timing(NOW)
    assert timing.age_seconds is None and timing.age_status == "time_unknown"
    assert timing.published_at is None
    assert source_timing(NOW, observed=False).age_status == "missing"


def test_av_marks_nonrunner_but_does_not_remove_history_or_infer_reason_category() -> None:
    out = current_entry_observations(
        ENTRIES,
        [
            {
                "source_record_type": "AV",
                "change_type": "withdrawal",
                "horse_number": 1,
                "announced_at": "2024-01-06T13:00:00",
                "reason_code": "001",
                "reason_name": "疾病",
                "artifact_hash": "av-source",
            }
        ],
        analysis_at=NOW,
        scheduled_start_at=START,
    )
    assert len(out.runners) == 2 and not out.history_entries_removed
    first, second = out.runners
    assert first.betting_eligible is False and first.runner_status == "withdrawn_or_excluded"
    assert first.withdrawal_notice.artifact_hash == "av-source"
    # A result-side abnormality on the original card is not a pre-race announcement.
    assert second.betting_eligible is None and second.runner_status == "no_withdrawal_notice"
    assert out.confirmed_nonrunner_count == 1


def test_latest_confirmed_jc_updates_current_jockey_and_weight_in_publication_order() -> None:
    rows = [
        {
            "source_record_type": "JC",
            "change_type": "jockey_change",
            "horse_number": 1,
            "announced_at": "2024-01-06T14:00:00+09:00",
            "jockey_after_id": "first",
            "jockey_after_name": "First",
            "weight_after_kg": 54.0,
        },
        {
            "source_record_type": "JC",
            "change_type": "jockey_change",
            "horse_number": 1,
            "announced_at": "2024-01-06T05:15:00+00:00",
            "jockey_after_id": "second",
            "jockey_after_name": "Second",
            "weight_after_kg": 53.0,
        },
    ]
    out = current_entry_observations(ENTRIES, list(reversed(rows)), analysis_at=NOW, scheduled_start_at=START)
    assert out.runners[0].jockey_id == "second" and out.runners[0].assigned_weight_kg == 53.0
    assert ENTRIES[0]["jockey_id"] == "original"


@pytest.mark.parametrize("announced,retained", [(None, True), ("2024-01-06T14:45:00", False)])
def test_unknown_and_future_notice_times_are_not_applied(announced: str | None, retained: bool) -> None:
    out = current_entry_observations(
        ENTRIES,
        [
            {
                "source_record_type": "AV",
                "change_type": "withdrawal",
                "horse_number": 1,
                "announced_at": announced,
            }
        ],
        analysis_at=NOW,
        scheduled_start_at=START,
    )
    assert out.runners[0].betting_eligible is None
    assert bool(out.changes) is retained
    if retained:
        assert out.unverified_notice_count == 1 and out.changes[0].unapplied_reason == "announcement_time_unknown"


def test_post_start_notice_does_not_become_pre_start_evidence() -> None:
    out = current_entry_observations(
        ENTRIES,
        [
            {
                "source_record_type": "AV",
                "change_type": "withdrawal",
                "horse_number": 1,
                "announced_at": "2024-01-06T15:01:00",
            }
        ],
        analysis_at="2024-01-06T16:00:00+09:00",
        scheduled_start_at=START,
    )
    assert out.changes == []


@pytest.mark.parametrize(
    "rows,expected", [([], "race_unavailable"), ([{"target_horses": 0, "available_horses": 0}], "no_declared_entries")]
)
def test_empty_precomputation_is_not_marked_ready(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]], expected: str
) -> None:
    monkeypatch.setattr(_speed_read_model, "fetch_rows", AsyncMock(return_value=rows))
    out = asyncio.run(_speed_read_model.get_speed_quality_status(object(), "2024010605010111"))
    assert out.status == expected and not out.ready
    with pytest.raises(ValueError, match=expected):
        asyncio.run(_speed_read_model.ensure_speed_quality_available(object(), "2024010605010111"))


def test_unreadable_optional_model_store_does_not_block_forecast_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    monkeypatch.setattr(
        model_status, "get_speed_quality_status", AsyncMock(side_effect=SQLAlchemyError("private detail"))
    )
    out = asyncio.run(model_status.load_prediction_model_status(object(), "2024010605010111", analysis_at=NOW))
    assert out.speed_quality is None
    assert out.check_error == "model_prerequisite_check_failed:SQLAlchemyError"
    assert all(not tool.prerequisites_ready for tool in out.tools)
    assert "private detail" not in out.model_dump_json()
