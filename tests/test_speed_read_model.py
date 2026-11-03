"""速度read modelが未整備のレースを予測へ混ぜないための試験。"""

from __future__ import annotations

import asyncio

import pytest

from jvdata_bridge_mcp.tools.prediction import _speed_read_model


@pytest.mark.parametrize("available", [0, 1])
def test_incomplete_speed_rows_fail_before_prediction(
    monkeypatch: pytest.MonkeyPatch,
    available: int,
) -> None:
    """対象時点の速度行が1頭でも欠けたら、中立値の推測で予測を続けない。"""

    captured: dict[str, object] = {}

    async def fake_fetch_rows(
        _database: object,
        sql: str,
        parameters: dict[str, object],
        **_kwargs: object,
    ) -> list[dict[str, object]]:
        captured.update(parameters)
        assert "stored.race_date = t.race_date" in sql
        assert "stored.history_cutoff_date" not in sql
        return [{"history_before_date": "2026-09-27", "target_horses": 2, "available_horses": available}]

    monkeypatch.setattr(_speed_read_model, "fetch_rows", fake_fetch_rows)
    with pytest.raises(ValueError, match=f"missing {2 - available} of 2 declared entries"):
        asyncio.run(_speed_read_model.ensure_speed_quality_available(object(), "2026092706040911"))
    assert "as_of_datetime" not in captured
    assert captured["race_code"] == "2026092706040911"


def test_complete_speed_rows_allow_prediction(monkeypatch: pytest.MonkeyPatch) -> None:
    """全出走馬の対象時点・対象モデルの行があれば通す。"""

    async def fake_fetch_rows(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [{"history_before_date": "2026-09-27", "target_horses": 2, "available_horses": 2}]

    monkeypatch.setattr(_speed_read_model, "fetch_rows", fake_fetch_rows)
    asyncio.run(_speed_read_model.ensure_speed_quality_available(object(), "2026092706040911"))
