"""着順原票のゼロ始まり、期間と条件の重複を実際のSQLで再現する。"""

import sqlite3
from datetime import date

import pytest

from jvdata_bridge_mcp.tools._people import surface_code, venue_code
from jvdata_bridge_mcp.tools._race_boundary import period_dates
from jvdata_bridge_mcp.tools.trainer.performance import _SQL


def test_trainer_counts_single_period_and_overall_condition() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH DATABASE ':memory:' AS jv_mart")
    db.execute("CREATE TABLE jv_mart.trainers (trainer_id, trainer_name, is_active, affiliation_name, licensed_date)")
    db.execute(
        "CREATE TABLE jv_mart.people_statistics (person_type, person_id, finish_index, finish_count, "
        "flat_prize_money_yen, period_index, condition_code)"
    )
    db.execute("INSERT INTO jv_mart.trainers VALUES ('t','trainer',1,'miho','2006-01-01')")
    for period in (1, 2, 3):
        for condition in ("flat_total", "turf_1600_or_less", "venue_06"):
            for index, count in enumerate([4, 5, 6, 7, 8, 9]):
                db.execute(
                    "INSERT INTO jv_mart.people_statistics VALUES ('trainer','t',?,?,?,?,?)",
                    (index, count, 1000, period, condition),
                )
    row = db.execute(_SQL, dict(trainer_code="t", period_index=3)).fetchone()
    assert row[5:9] == (39, 4, 15, 1000)
    db.close()


def test_exact_condition_inputs_and_date_cutoff() -> None:
    assert venue_code("中山競馬場") == venue_code("06") == "06"
    assert surface_code("芝") == surface_code("turf") == "turf"
    assert period_dates(date(2026, 9, 22), "current_year") == ("2026-01-01", "2027-01-01")
    assert period_dates(date(2026, 9, 22), "previous_year") == ("2025-01-01", "2026-01-01")
    with pytest.raises(ValueError):
        venue_code("somewhere")
