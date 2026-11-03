"""小さな架空レースで、評価側も整合した分布を計算することを確認する。"""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")


def test_vectorized_joint_scores_agree_with_runtime_distribution() -> None:
    from jvdata_bridge_mcp.ml.distribution import finish_distribution
    from jvdata_bridge_mcp.ml.research.metrics import probability_metrics

    p = np.asarray([0.4, 0.3, 0.2, 0.1])
    frame = pd.DataFrame({"race_id": ["synthetic"] * 4, "finish": [1, 2, 3, 4], "win": [1, 0, 0, 0]})
    powers = (0.8, 0.7)
    dist = finish_distribution([0, 1, 2, 3], p, stage_powers=powers)
    metrics = probability_metrics(frame, p, stage_powers=powers)
    assert metrics["joint_log_loss"] == pytest.approx(-math.log(dist["tickets"]["trifecta"][(0, 1, 2)]))
    assert metrics["quinella_log_loss"] == pytest.approx(-math.log(dist["tickets"]["quinella"][(0, 1)]))
    assert metrics["trio_log_loss"] == pytest.approx(-math.log(dist["tickets"]["trio"][(0, 1, 2)]))
    wide_brier = sum((v - float(all(i < 3 for i in key))) ** 2 for key, v in dist["tickets"]["wide"].items()) / 6
    assert metrics["wide_brier"] == pytest.approx(wide_brier)
