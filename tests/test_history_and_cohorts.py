"""古い条件一致走、条件別コース参照、区間の長さと位置履歴を検証する。"""

import asyncio
from datetime import date

import pytest

from jvdata_bridge_mcp.tools.pace import course_profile
from jvdata_bridge_mcp.tools.pace._configuration import course_configuration
from jvdata_bridge_mcp.tools.prediction.positions import position_history_summary
from jvdata_bridge_mcp.tools.race import recent_form
from jvdata_bridge_mcp.tools.race._laps import lap_window_comparison
from jvdata_bridge_mcp.tools.race.forecast_context import _shared_facts


def test_condition_history_keeps_older_negative_and_shares_fact_without_reordering_recent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def read(db: object, sql: str, params: dict[str, object]) -> list[dict[str, object]]:
        assert params["condition_runs"] == 3
        assert sql.index("selected_runs AS MATERIALIZED") < sql.index("history_class.class_name")
        common = dict(
            horse_number=1,
            horse_id="a",
            horse_name="A",
            running_style="unknown",
            condition_coverage={"stored_jra_starts": 26},
        )
        return [
            dict(common, fact_id="recent", run_number=1, selection_reasons=["recent", "same_surface"]),
            dict(
                common, fact_id="old", run_number=26, finish_position=10, selection_reasons=["same_surface_distance"]
            ),
        ]

    monkeypatch.setattr(recent_form, "fetch_rows", read)
    history = asyncio.run(recent_form.build_race_recent_form_facts_tool(object())("r", condition_runs=3))
    assert [r.fact_id for r in history.runs] == ["recent"]
    assert [r.fact_id for r in history.condition_runs] == ["recent", "old"]
    facts, horses = _shared_facts(history, None, None)
    assert facts["old"]["finish_position"] == 10
    assert horses[0]["run_fact_ids"] == ["recent"]
    assert horses[0]["condition_run_fact_ids"] == ["recent", "old"]
    assert len(facts) == 2


def test_cohort_filters_keep_grade_age_signature_and_distinct_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def read(db: object, sql: str, params: dict[str, object], **kwargs: object) -> list[dict[str, object]]:
        calls.append(params)
        if sql == course_profile._TARGET_SQL:
            return [
                dict(
                    venue_code="07",
                    track_code="11",
                    race_date="2025-08-24",
                    grade_code="",
                    race_symbol_name="[指定]",
                    class_fact={"condition_codes_by_age": {"2": "701", "3": "000", "youngest": "701"}},
                )
            ]
        if sql == course_profile._MATCHED_SQL:
            assert params["target_condition_codes"] == '{"2": "701"}'
            assert params["target_grade_code"] == ""
            assert params["target_female_only"] is False
            return [dict(race_count=3, distance_meters=2000, track_code="11")]
        return [dict(race_count=100, distance_meters=2000, track_code="11")]

    monkeypatch.setattr(course_profile, "fetch_rows", read)
    output = asyncio.run(
        course_profile.build_race_pace_course_profile_tool(object())("r", history_start_date="2020-01-01")
    )
    assert output.profiles[0].race_count == 100 and output.matched_profiles[0].race_count == 3
    assert output.filters["history_start_date"] == "2020-01-01"
    assert output.matched_reference_status == "available"
    assert "not_exact_runner_age_or_field_size" in output.matched_profiles[0].reference_scope


def test_missing_target_conditions_do_not_claim_matched_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    async def read(db: object, sql: str, params: dict[str, object], **kwargs: object) -> list[dict[str, object]]:
        if sql == course_profile._TARGET_SQL:
            return [dict(venue_code="05", track_code="11", race_date="2025-01-01", class_fact={})]
        assert sql == course_profile._SQL
        return []

    monkeypatch.setattr(course_profile, "fetch_rows", read)
    output = asyncio.run(course_profile.build_race_pace_course_profile_tool(object())("r"))
    assert output.matched_profiles == [] and output.matched_reference_status == "target_age_conditions_unknown"
    assert output.course_configuration["status"] == "configuration_boundary_unverified"


def test_female_and_mixed_symbols_are_not_equivalent() -> None:
    assert course_profile.female_only_symbol("(国際) 牝 (指定)") is True
    assert course_profile.female_only_symbol("(国際) 牡・牝 (指定)") is False
    assert course_profile.female_only_symbol("(指定)") is False
    assert course_profile.female_only_symbol(None) is None


def test_lap_windows_report_overlap_missing_middle_and_odd_lengths() -> None:
    assert lap_window_comparison(1000, "10", 3)["overlap_meters"] == 200
    assert lap_window_comparison(1150, "24", 3)["opening_meters"] == 550
    assert lap_window_comparison(1150, "24", 3)["equal_length_windows"] is False
    assert lap_window_comparison(1400, "11", 3)["uncovered_middle_meters"] == 200
    assert lap_window_comparison(2880, "52", 3)["status"] == "unavailable"
    assert course_configuration("07", "11", "2011-01-01")["history_start_date"] is None
    assert course_configuration("09", "18", "2025-01-01")["history_start_date"] == "2006-12-02"


def test_position_summary_retains_recent_shift_and_zero_condition_sample() -> None:
    history = [dict(early=0.0, fourth=0.0, lead=0, surface="turf", distance=1600, race_date=date(2025, 1, 1))]
    history += [dict(early=1.0, fourth=1.0, lead=1, surface="turf", distance=1200, race_date=date(2024, 1, 1))] * 6
    output = position_history_summary(history, "dirt", 1200)
    assert output["recent5"]["mean_early_frontness"] != output["available_max20"]["mean_early_frontness"]
    assert output["same_surface_distance"]["n"] == 0
    assert output["same_surface_distance"]["mean_early_frontness"] is None
