"""一括contextの境界、参照共有、観察データの時点を検証する。"""

import asyncio
from datetime import date

import pytest

from jvdata_bridge_mcp.tools import _runtime
from jvdata_bridge_mcp.tools.race.forecast_context import Observation, eligible_observations


def test_race_code_date_does_not_depend_on_scheduled_start() -> None:
    from jvdata_bridge_mcp.tools._race_date import race_date_from_code

    assert race_date_from_code("2026092205040111") == date(2026, 9, 22)
    for code in ("r", "2026023005040111", "２０２６０９２２０５０４０１１１"):
        with pytest.raises(ValueError, match="race_code"):
            race_date_from_code(code)


def test_observation_requires_a_prior_date_without_ingestion_time() -> None:
    target_date = date(2026, 9, 22)
    obs = Observation(
        race_code="2026092205040111",
        horse_number=1,
        kind="training_effort",
        observed_date="2026-09-21",
        source="human",
        description="追い方の観察",
    )
    assert eligible_observations([obs], obs.race_code, {1}, target_date) == [obs]
    obs.observed_date = target_date
    with pytest.raises(ValueError, match="observed_at"):
        eligible_observations([obs], obs.race_code, {1}, target_date)


def test_request_cache_is_scoped_and_does_not_share_mutable_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def db_read(
        database: object, sql: str, payload: dict[str, object], **kwargs: object
    ) -> list[dict[str, object]]:
        calls.append((sql, payload))
        return [{"nested": {"n": 1}}]

    monkeypatch.setattr(_runtime, "_fetch_db_rows", db_read)

    async def run() -> None:
        with _runtime.forecast_read_session() as session:
            sql = "SELECT h.* FROM jv_mart.horse_runs h WHERE h.race_date < t.race_date"
            first = await _runtime.fetch_rows(object(), sql, {})
            first[0]["nested"]["n"] = 99
            second = await _runtime.fetch_rows(object(), sql, {})
            assert second[0]["nested"]["n"] == 1
            assert session.query_count == 1
            assert "h.race_date < t.race_date" in calls[0][0]
            assert "context_cutoff_date" not in calls[0][1]
        await _runtime.fetch_rows(object(), "SELECT different", {})

    asyncio.run(run())
    assert len(calls) == 2


def test_shared_fact_graph_preserves_class_age_and_single_training_session() -> None:
    from jvdata_bridge_mcp.tools.race.forecast_context import _compact_training, _share_race_and_class_facts

    cl = {"source_race_code": "past", "applicable_horse_age": 3, "class_name": "1勝クラス"}
    facts = {
        "run:a": {"recent_race_code": "past", "class_fact": dict(cl), "race_laps": [12, 13], "field_size": 12},
        "run:b": {"recent_race_code": "past", "class_fact": dict(cl), "race_laps": [12, 13], "field_size": 12},
    }
    _share_race_and_class_facts({}, facts, [])
    assert facts["run:a"]["class_fact_id"] == facts["run:b"]["class_fact_id"]
    assert facts["run:a"]["race_fact_id"] == facts["run:b"]["race_fact_id"]
    assert "race_laps" not in facts["run:a"]
    assert facts["jra-race:past"]["race_laps"] == [12, 13]
    session = {"source_record_id": "source:1", "datetime": "2026-09-21T06:00:00", "time_4f": 52.0}
    horse = {
        "horse_number": "01",
        "recent_history": [dict(session)],
        "best_times": {"f4": {"session": dict(session)}},
        "best_sessions_by_cohort": [{"session": dict(session)}],
    }
    out = _compact_training({"items": [horse]}, facts)
    ref = out["items"][0]["session_fact_ids"][0]
    assert (
        ref == horse["best_times"]["f4"]["session_fact_id"] == horse["best_sessions_by_cohort"][0]["session_fact_id"]
    )
    assert facts[ref] == session


def test_target_race_running_observations_are_rejected_even_with_a_false_prior_date() -> None:
    obs = Observation(
        race_code="2026092205040111",
        evidence_race_code="2026092205040111",
        horse_number=1,
        kind="start",
        observed_date="2026-09-21",
        source="human",
        description="jump from gate",
    )
    with pytest.raises(ValueError, match="evidence race"):
        eligible_observations([obs], obs.race_code, {1}, date(2026, 9, 22))


def test_same_day_post_race_knowledge_is_after_forecast_without_publication_date() -> None:
    from jvdata_bridge_mcp.tools.race.forecast_context import evidence_after_target_date

    cutoff = date.fromisoformat("2026-09-22")
    assert evidence_after_target_date({"end": "2026-09-22", "evidence_timing": "post_race_results"}, cutoff)
    assert evidence_after_target_date({"end": "2026-09-23", "evidence_timing": "unknown"}, cutoff)
    assert not evidence_after_target_date({"end": "2026-09-22", "evidence_timing": "unknown"}, cutoff)
    assert not evidence_after_target_date({"end": "2026-09-21", "evidence_timing": "post_race_results"}, cutoff)
