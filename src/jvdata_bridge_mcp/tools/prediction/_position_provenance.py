"""同梱位置モデルの学習・評価期間、適用域と較正状態。"""

from jvdata_bridge_mcp.tools.prediction._positions_coefficients import POSITION_HYBRID_MODEL_VERSION


def position_model_provenance() -> dict[str, object]:
    return {
        "model_version": POSITION_HYBRID_MODEL_VERSION,
        "final_fit_period": {"start": "2012-01-01", "end": "2024-12-31"},
        "configuration_selection_period": {"start": "2023-01-01", "end": "2024-12-31"},
        "previous_development_seen_periods": [
            {"start": "2025-01-01", "end": "2025-12-31"},
            {"start": "2026-01-01", "end": "2026-09-27"},
        ],
        "support_status": "2025_and_2026_retrospective; not_independent_unseen_evaluation",
        "calibration": "simulation_quantiles_and_probabilities_not_empirically_calibrated",
        "development_evaluation_scope": "8_to_18_flat_corner_races; starters_equal_finishers; nonfinishers_withdrawals_straight_and_jump_excluded",
        "runtime_roster_scope": "declared_entries; withdrawal_timing_unconfirmed; different_from_complete_finisher_evaluation_scope",
        "reference": "docs/forecast-context.md",
        "training_required_for_clone": False,
    }
