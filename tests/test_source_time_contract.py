"""予測は確認できる日時だけを使い、取得時刻を推測しない。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlglot

from jvdata_bridge_mcp.tools._source_timing import source_timing
from jvdata_bridge_mcp.tools.race._entry_changes import current_entry_observations
from jvdata_bridge_mcp.tools.race._weather import reconstruct_weather
from jvdata_bridge_mcp.tools.race._weight_source import WEIGHT_CTES
from jvdata_bridge_mcp.tools.race.condition_changes import _SQL as CONDITION_CHANGES_SQL


def test_runtime_uses_only_supported_source_time_fields() -> None:
    source = Path(__file__).resolve().parents[1] / "src" / "jvdata_bridge_mcp"
    offenders = [
        str(path.relative_to(source))
        for path in source.rglob("*.py")
        if any(column in path.read_text(encoding="utf-8") for column in ("available_at", "history_cutoff_date"))
    ]
    assert offenders == []


def test_condition_change_start_time_comes_from_the_actual_scheduled_timestamp() -> None:
    assert "CAST(r.scheduled_start_at AS time) AS scheduled_start_time" in CONDITION_CHANGES_SQL
    assert "r.scheduled_start_time" not in CONDITION_CHANGES_SQL


def test_unknown_observation_time_is_not_replaced_with_analysis_or_stale_extra_fields() -> None:
    now = "2024-01-06T14:30:00+09:00"
    out = current_entry_observations(
        [{"horse_number": 1, "horse_id": "h", "horse_name": "Horse"}],
        [{"source_record_type": "AV", "change_type": "withdrawal", "horse_number": 1, "available_at": now}],
        analysis_at=now,
        scheduled_start_at="2024-01-06T15:00:00+09:00",
    )
    assert not out.changes[0].applied
    assert out.changes[0].source_timing.published_at is None
    assert "retrieved_at" not in out.changes[0].source_timing.model_dump()
    assert out.runners[0].betting_eligible is None
    timing = source_timing(now, published_at="2024-01-06T14:00:00")
    assert timing.age_seconds == 1800
    assert not {"retrieved_at", "retrieval_age_seconds", "ingestion_delay_seconds"} & timing.model_dump().keys()


def test_weather_uses_announcement_and_record_sequence_without_invented_ingestion_order() -> None:
    first = {
        "observation_date": "2024-01-06",
        "announced_at": "2024-01-06T14:00:00",
        "source_sequence": 9,
        "current_weather_code": "1",
        "current_weather_name": "晴",
        "available_at": "2099-01-01T00:00:00",
    }
    second = {
        **first,
        "source_sequence": 10,
        "current_weather_code": "2",
        "current_weather_name": "曇",
        "available_at": None,
    }
    result = reconstruct_weather([second, first], analysis_at="2024-01-06T14:30:00+09:00")
    assert result["current_weather_name"] == "曇"
    timing = result["field_observations"]["weather"]["source_timing"]
    assert timing["age_seconds"] == 1800 and "retrieved_at" not in timing


def test_raw_wh_with_ambiguous_versions_does_not_treat_a_hash_as_a_timestamp() -> None:
    statement = sqlglot.parse_one("WITH " + WEIGHT_CTES + " SELECT * FROM wh_artifact", read="postgres")
    artifact = next(cte for cte in statement.find_all(sqlglot.exp.CTE) if cte.alias == "wh_artifact")
    query = artifact.this.sql(dialect="sqlite").replace('"jv_data".', "")
    with sqlite3.connect(":memory:") as connection:
        connection.execute("ATTACH DATABASE ':memory:' AS jv_data")
        connection.execute(
            "CREATE TABLE jv_data.source_group_values(record_type, natural_key, child_field_name, artifact_hash)"
        )
        connection.execute("INSERT INTO jv_data.source_group_values VALUES ('WH','key','馬番','a')")
        assert connection.execute(query, {"wh_natural_key": "key"}).fetchall() == [("a",)]
        connection.execute("INSERT INTO jv_data.source_group_values VALUES ('WH','key','馬番','z')")
        assert connection.execute(query, {"wh_natural_key": "key"}).fetchall() == []
