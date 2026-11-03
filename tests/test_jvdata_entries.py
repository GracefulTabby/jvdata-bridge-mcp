"""出馬表の正本と1頭1行契約を検証する。"""

from __future__ import annotations

from jvdata_bridge_mcp.tools.race.entries import _SQL as ENTRIES_SQL


def test_entries_list_uses_canonical_cards_without_changing_target_fields() -> None:
    assert "FROM jv_mart.race_cards AS c" in ENTRIES_SQL
    assert "SELECT DISTINCT ON (c.race_id, c.horse_number)" in ENTRIES_SQL
    assert "c.horse_number BETWEEN 1 AND 18" in ENTRIES_SQL
    for field in ("body_weight", "abnormality", "final_win", "finish_position"):
        assert field not in ENTRIES_SQL
