"""同名祖先の区別、自己実績除外、母系のゼロ標本を検証する。"""

import asyncio
import json

import pytest

from jvdata_bridge_mcp.tools.bloodline import distance_bias as module


def test_family_evidence_uses_stable_parent_ids_and_excludes_target(monkeypatch: pytest.MonkeyPatch) -> None:
    async def read(database: object, sql: str, params: dict[str, object], **kwargs: object) -> list[dict[str, object]]:
        if sql == module._TARGET_SQL:
            return [
                dict(
                    horse_number=1,
                    horse_id="target",
                    horse_name="T",
                    race_date="2026-09-22",
                    distance_meters=1800,
                    surface="turf",
                )
            ]
        if sql == module._PARENT_SQL:
            return [
                dict(
                    horse_id="target",
                    pedigree_three_generations=[
                        {"f018_a": "sire01", "f018_b": "same_name"},
                        {"f018_a": "dam01", "f018_b": "same_name"},
                    ],
                )
            ]
        if sql == module._RELATIVE_SQL:
            return [
                dict(horse_id="target", sire_id="sire01", dam_id="dam01"),
                dict(horse_id="sibling", sire_id="sire01", dam_id="dam02"),
                dict(horse_id="unrelated", sire_id="sire02", dam_id="dam02"),
            ]
        if sql == module._SQL:
            links = json.loads(params["links"])
            assert links == [{"family_key": "target:sire:sire01", "horse_id": "sibling"}]
            assert params["relative_horse_ids"] == ["sibling"]
            return [dict(family_key="target:sire:sire01", starts=10, wins=2, top3_finishes=4, sample_horses=1)]
        assert sql == module._BASELINE_SQL
        return [{"rate": 0.1}]

    monkeypatch.setattr(module, "fetch_rows", read)
    rows = asyncio.run(module.build_bloodline_distance_bias_tool(object())("r")).biases
    sire, dam = rows
    assert sire.raw_win_rate == 0.2
    assert sire.shrunk_win_rate == pytest.approx(4 / 30)
    assert sire.sample_horses == 1 and sire.target_excluded
    assert dam.starts == 0 and dam.raw_win_rate is None and dam.shrunk_win_rate is None
    assert sire.ancestor_breeding_id != dam.ancestor_breeding_id
