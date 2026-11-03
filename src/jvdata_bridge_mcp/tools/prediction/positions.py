"""芝・ダート平地戦の馬別コーナー位置シナリオ。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from itertools import combinations
from math import log

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.prediction._positions_coefficients import POSITION_HYBRID_MODELS
from jvdata_bridge_mcp.tools.prediction._positions_core import (
    POSITION_SIMULATIONS,
    expanded_position_features,
    horse_long_front_strength,
    predict_hybrid_position,
    simulate_hybrid_position_distributions,
)
from jvdata_bridge_mcp.tools.prediction.features import PREDICTION_STATEMENT_TIMEOUT_SECONDS


class PositionScenarioUnavailable(ValueError):
    """対象レースが馬別位置モデルの検証範囲外である。"""


class HorsePositionScenario(BaseModel):
    """各馬の最初に記録されたコーナーと4コーナーの事前位置分布。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: int = Field(description="馬番")
    horse_name: str = Field(description="馬名")
    history_races: int = Field(description="特徴量に使えた馬の過去走数。最大20")
    history_coverage: float = Field(description="馬の過去20走に対する履歴被覆率")
    jockey_history_rides: int = Field(description="特徴量に使えた騎手の過去騎乗数。最大50")
    early_expected_rank: float = Field(description="最初に記録されたコーナーの仮想順位の平均")
    early_lead_probability: float = Field(description="最初に記録されたコーナーで先頭となる確率")
    early_top3_probability: float = Field(description="最初に記録されたコーナーで前3頭に入る確率")
    early_position_band_probabilities: dict[str, float] = Field(
        description="最初の記録コーナーの位置帯。lead=1位、forward=2位〜頭数の上位1/3、midfield=次の1/3、rear=残り"
    )
    fourth_expected_rank: float = Field(description="4コーナーの仮想順位の平均")
    fourth_lead_probability: float = Field(description="4コーナーで先頭となる確率")
    fourth_top3_probability: float = Field(description="4コーナーで前3頭に入る確率")
    fourth_position_band_probabilities: dict[str, float] = Field(description="4コーナーの位置帯。区切りはearlyと同じ")
    early_rank_probabilities: list[float] = Field(default_factory=list)
    fourth_rank_probabilities: list[float] = Field(default_factory=list)
    early_rank_interval_80: list[int] = Field(default_factory=list)
    fourth_rank_interval_80: list[int] = Field(default_factory=list)
    interval_semantics: str = "simulation_quantiles_10_to_90; empirical_coverage_not_calibrated"
    zero_history: bool = False
    same_surface_history_races: int = 0
    domain_shift_flags: list[str] = Field(default_factory=list)
    historical_average_advance: float | None = None
    historical_makuri_proxy_rate: float | None = None
    historical_makuri_proxy_count: int = 0
    historical_makuri_proxy_observations: int = 0
    makuri_semantics: str = (
        "past_corner1_to3_first_rank>3_to_fourth<=3_with_gain>=3; descriptive_proxy_not_tactical_probability"
    )
    position_history_summary: dict[str, dict[str, object]] = Field(default_factory=dict)
    prediction_scope: str = "uncalibrated_marginal_corner_positions; not_tactics_or_pace_or_finish"
    progression_semantics: str = "observed_early_to_fourth_normalized_position_change; not_joint_prediction"


class ConditionalEarlyPosition(BaseModel):
    """指定馬が最初の記録コーナーで先頭だった場合の各馬の位置。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: int = Field(description="馬番")
    horse_name: str = Field(description="馬名")
    expected_rank: float = Field(description="条件付きの最初の記録コーナーでの期待順位")
    top3_probability: float = Field(description="条件付きの同コーナーで前3頭に入る確率")
    position_band_probabilities: dict[str, float] = Field(description="条件付きの同コーナーの位置帯確率")


class AlternativeLeaderScenario(BaseModel):
    """先頭候補を一頭固定した場合の最初の記録コーナーの代替隊列。"""

    model_config = ConfigDict(extra="forbid")
    conditional_scope: str = "first_recorded_corner_only"
    unavailable_components: list[str] = Field(
        default_factory=lambda: ["conditional_fourth_corner", "conditional_pace"]
    )

    leader_horse_number: int = Field(description="先頭と仮定した馬番")
    leader_horse_name: str = Field(description="先頭と仮定した馬名")
    leader_probability: float = Field(description="基準モデルでその馬が先頭になる確率")
    seed: int = Field(description="この分岐の順位生成に用いたseed")
    simulations: int = Field(description="この分岐の仮想順位生成回数")
    horses: list[ConditionalEarlyPosition] = Field(description="その馬が先頭の場合の各馬の最初の記録コーナーの位置")


class FrontPairScenario(BaseModel):
    """先頭候補二頭が序盤の前2頭にいると仮定した隊列。"""

    model_config = ConfigDict(extra="forbid")
    conditional_scope: str = "first_recorded_corner_only"
    unavailable_components: list[str] = Field(
        default_factory=lambda: ["joint_probability", "conditional_fourth_corner", "conditional_pace"]
    )

    horse_numbers: list[int] = Field(description="最初の記録コーナーで前2頭と仮定した馬番")
    horse_names: list[str] = Field(description="その二頭の馬名")
    seed: int = Field(description="この分岐の順位生成に用いたseed")
    simulations: int = Field(description="この分岐の仮想順位生成回数")
    horses: list[ConditionalEarlyPosition] = Field(description="二頭が前2頭の場合の各馬の同コーナーの位置")


_HISTORICAL_CUTOFF_DATE = "c.race_date"
_SQL = f"""
WITH target_cards AS MATERIALIZED (
    SELECT DISTINCT ON (c.race_id, c.horse_number)
        c.horse_number,
        c.horse_name,
        c.horse_id,
        c.race_date,
        c.horse_age,
        c.jockey_id,
        r.venue_code,
        r.track_code,
        r.distance_meters
    FROM jv_mart.race_cards AS c
    JOIN jv_data.races AS r ON r.race_id = c.race_id
    WHERE c.race_id = :race_code
      AND c.horse_number IS NOT NULL

    ORDER BY c.race_id, c.horse_number, c.horse_id
)
SELECT
    c.horse_number, c.horse_name, c.horse_id, c.jockey_id, c.horse_age,
    c.race_date AS target_race_date,
    c.venue_code, c.track_code, c.distance_meters,
    h.history_race_date, h.history_race_id, h.history_track_code, h.history_distance_meters,
    h.history_field_size, h.early_position, h.fourth_position, h.early_corner_number
FROM target_cards AS c
LEFT JOIN LATERAL (
    SELECT
        h.race_date AS history_race_date,
        h.race_id AS history_race_id,
        h.track_code AS history_track_code,
        h.distance_meters AS history_distance_meters,
        (
            SELECT COUNT(*)
            FROM jv_mart.race_results AS rr
            WHERE rr.race_id = h.race_id
              AND rr.is_final_result AND rr.is_complete_result
              AND rr.is_starter AND rr.is_finisher
        ) AS history_field_size,
        COALESCE(h.corner_1_position, h.corner_2_position,
                 h.corner_3_position, h.corner_4_position) AS early_position,
        CASE WHEN h.corner_1_position IS NOT NULL THEN 1 WHEN h.corner_2_position IS NOT NULL THEN 2
             WHEN h.corner_3_position IS NOT NULL THEN 3 ELSE 4 END AS early_corner_number,
        h.corner_4_position AS fourth_position
    FROM jv_mart.horse_runs AS h
    WHERE h.horse_id = c.horse_id
      AND h.race_date < {_HISTORICAL_CUTOFF_DATE}
      AND h.is_final_result AND h.is_complete_result
      AND h.is_starter AND h.is_finisher
      AND LEFT(COALESCE(h.track_code, ''), 1) IN ('1', '2')
      AND COALESCE(h.corner_1_position, h.corner_2_position,
                   h.corner_3_position, h.corner_4_position) IS NOT NULL
      AND h.corner_4_position IS NOT NULL
    ORDER BY h.race_date DESC, h.race_id DESC
    LIMIT 20
) AS h ON TRUE
ORDER BY c.horse_number, h.history_race_date DESC NULLS LAST, h.history_race_id DESC NULLS LAST
"""

_JOCKEY_SQL = """
WITH target_jockeys AS MATERIALIZED (
    SELECT DISTINCT c.jockey_id, c.race_date
    FROM jv_mart.race_cards AS c
    WHERE c.race_id = :race_code
      AND c.horse_number IS NOT NULL
      AND c.jockey_id IS NOT NULL

), ranked_entries AS MATERIALIZED (
    SELECT
        e.jockey_id, e.race_id, e.horse_id, e.race_date
    FROM jv_mart.entry_performances AS e
    JOIN target_jockeys AS t ON t.jockey_id = e.jockey_id
    WHERE e.race_date < t.race_date
), valid_history AS MATERIALIZED (
    SELECT
        e.jockey_id,
        e.race_date AS history_race_date,
        e.race_id AS history_race_id,
        rr.track_code AS history_track_code,
        rr.distance_meters AS history_distance_meters,
        (
            SELECT COUNT(*)
            FROM jv_mart.race_results AS peers
            WHERE peers.race_id = e.race_id
              AND peers.is_final_result AND peers.is_complete_result
              AND peers.is_starter AND peers.is_finisher
        ) AS history_field_size,
        COALESCE(rr.corner_1_position, rr.corner_2_position,
                 rr.corner_3_position, rr.corner_4_position) AS early_position,
        CASE WHEN rr.corner_1_position IS NOT NULL THEN 1 WHEN rr.corner_2_position IS NOT NULL THEN 2
             WHEN rr.corner_3_position IS NOT NULL THEN 3 ELSE 4 END AS early_corner_number,
        rr.corner_4_position AS fourth_position,
        ROW_NUMBER() OVER (
            PARTITION BY e.jockey_id ORDER BY e.race_date DESC, e.race_id DESC
        ) AS valid_rank
    FROM ranked_entries AS e
    JOIN jv_mart.race_results AS rr
      ON rr.race_id = e.race_id AND rr.horse_id = e.horse_id
    WHERE rr.is_final_result AND rr.is_complete_result
      AND rr.is_starter AND rr.is_finisher
      AND LEFT(COALESCE(rr.track_code, ''), 1) IN ('1', '2')
      AND COALESCE(rr.corner_1_position, rr.corner_2_position,
                   rr.corner_3_position, rr.corner_4_position) IS NOT NULL
      AND rr.corner_4_position IS NOT NULL
)
SELECT * FROM valid_history
WHERE valid_rank <= 50
ORDER BY jockey_id, history_race_date DESC, history_race_id DESC
"""


def _normalized_history_run(row: dict[str, object]) -> dict[str, object] | None:
    history_size = row.get("history_field_size")
    early = row.get("early_position")
    fourth = row.get("fourth_position")
    race_date = row.get("history_race_date")
    distance = row.get("history_distance_meters")
    if (
        history_size is None
        or int(history_size) < 2
        or early is None
        or fourth is None
        or race_date is None
        or distance is None
    ):
        return None
    size = int(history_size)
    early_position = int(early)
    fourth_position = int(fourth)
    return {
        "early": (size - early_position) / (size - 1),
        "fourth": (size - fourth_position) / (size - 1),
        "lead": float(early_position == 1),
        "top3": float(early_position <= 3),
        "surface": "dirt" if str(row["history_track_code"]).startswith("2") else "turf",
        "distance": int(distance),
        "race_date": date.fromisoformat(str(race_date)),
        "early_rank": early_position,
        "fourth_rank": fourth_position,
        "early_corner_number": row.get("early_corner_number"),
    }


def position_history_summary(
    history: list[dict[str, object]], surface: str, distance: int
) -> dict[str, dict[str, object]]:
    """同じ正規化尺度で直近と条件一致の履歴を記述する。新しい予測・学習は行わない。"""

    groups = {
        "available_max20": history,
        "recent5": history[:5],
        "same_surface_distance": [r for r in history if r["surface"] == surface and r["distance"] == distance],
    }
    output = {}
    for name, runs in groups.items():
        n = len(runs)
        output[name] = {
            "n": n,
            "evidence_start_date": min(str(r["race_date"]) for r in runs) if n else None,
            "evidence_end_date": max(str(r["race_date"]) for r in runs) if n else None,
            "mean_early_frontness": round(sum(float(r["early"]) for r in runs) / n, 4) if n else None,
            "mean_fourth_frontness": round(sum(float(r["fourth"]) for r in runs) / n, 4) if n else None,
            "lead_count": sum(bool(r["lead"]) for r in runs),
            "frontness_scale": "0=last;1=first; normalized_by_past_field_size",
            "scope": "descriptive_subset_of_available_max20; not_all_career",
        }
    return output


async def forecast_position_scenarios(
    database: JvDataDatabase,
    race_code: str,
    *,
    include_branches: bool = True,
) -> tuple[list[HorsePositionScenario], int, list[AlternativeLeaderScenario], list[FrontPairScenario], float]:
    """対象日の結果やオッズを読まず、各馬の2地点の位置分布を返す。"""

    rows = await fetch_rows(
        database,
        _SQL,
        {"race_code": race_code},
        statement_timeout_seconds=PREDICTION_STATEMENT_TIMEOUT_SECONDS,
    )
    groups: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[int(row["horse_number"])].append(row)
    field_size = len(groups)
    if not 8 <= field_size <= 18:
        raise PositionScenarioUnavailable("position model requires 8–18 declared entries; withdrawal timing unknown")
    first = rows[0]
    track_code = str(first["track_code"] or "")
    if track_code[:1] not in ("1", "2") or first["distance_meters"] is None:
        raise PositionScenarioUnavailable("position model requires a flat turf/dirt race and known distance")
    if str(first["venue_code"]) == "04" and track_code == "10" and int(first["distance_meters"]) == 1000:
        raise PositionScenarioUnavailable("straight 1000 m course has no recorded corners")
    surface = "dirt" if track_code.startswith("2") else "turf"
    distance = int(first["distance_meters"])
    target_date = date.fromisoformat(str(first["target_race_date"]))
    venue_code = str(first["venue_code"])
    ordered_numbers = sorted(groups)
    horse_histories = {
        number: [run for row in groups[number] if (run := _normalized_history_run(row)) is not None]
        for number in ordered_numbers
    }
    jockey_rows = await fetch_rows(
        database,
        _JOCKEY_SQL,
        {"race_code": race_code},
        statement_timeout_seconds=PREDICTION_STATEMENT_TIMEOUT_SECONDS,
    )
    jockey_histories: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in jockey_rows:
        run = _normalized_history_run(row)
        if run is not None:
            jockey_histories[str(row["jockey_id"])].append(run)
    front_strengths = {number: horse_long_front_strength(horse_histories[number]) for number in ordered_numbers}
    total_front_strength = sum(front_strengths.values())
    qualities: dict[str, list[float]] = {"early": [], "fourth": []}
    lead_weights: dict[str, list[float]] = {"early": [], "fourth": []}
    history_counts: list[int] = []
    jockey_counts: list[int] = []
    for number in ordered_numbers:
        card = groups[number][0]
        history = horse_histories[number]
        jockey_id = str(card["jockey_id"]) if card["jockey_id"] is not None else ""
        jockey_history = jockey_histories.get(jockey_id, [])
        history_counts.append(len(history))
        jockey_counts.append(len(jockey_history))
        values = expanded_position_features(
            history,
            jockey_history,
            race_date=target_date,
            field_size=field_size,
            horse_number=number,
            horse_age=int(card["horse_age"]) if card["horse_age"] is not None else None,
            surface=surface,
            distance=distance,
            venue_code=venue_code,
            other_front_pressure=(total_front_strength - front_strengths[number]) / (field_size - 1),
        )
        for stage in qualities:
            quality, lead_weight = predict_hybrid_position(values, stage)
            qualities[stage].append(quality)
            lead_weights[stage].append(lead_weight)
    seed = int(race_code) % (2**32)
    early = simulate_hybrid_position_distributions(
        qualities["early"],
        lead_weights["early"],
        temperature=POSITION_HYBRID_MODELS["early"].temperature,
        simulations=POSITION_SIMULATIONS,
        seed=seed,
    )
    fourth = simulate_hybrid_position_distributions(
        qualities["fourth"],
        lead_weights["fourth"],
        temperature=POSITION_HYBRID_MODELS["fourth"].temperature,
        simulations=POSITION_SIMULATIONS,
        seed=seed + 1,
    )
    horses = [
        HorsePositionScenario(
            horse_number=number,
            horse_name=str(groups[number][0]["horse_name"]),
            history_races=history_counts[index],
            history_coverage=round(history_counts[index] / 20, 3),
            jockey_history_rides=jockey_counts[index],
            early_rank_probabilities=[round(p, 6) for p in early[index].rank_probabilities],
            fourth_rank_probabilities=[round(p, 6) for p in fourth[index].rank_probabilities],
            early_rank_interval_80=list(early[index].rank_interval_80),
            fourth_rank_interval_80=list(fourth[index].rank_interval_80),
            zero_history=history_counts[index] == 0,
            same_surface_history_races=sum(run["surface"] == surface for run in horse_histories[number]),
            position_history_summary=position_history_summary(horse_histories[number], surface, distance),
            domain_shift_flags=(["zero_history"] if history_counts[index] == 0 else [])
            + (
                ["no_same_surface_in_position_history"]
                if not any(run["surface"] == surface for run in horse_histories[number])
                else []
            ),
            historical_average_advance=(
                round(
                    sum(float(run["fourth"]) - float(run["early"]) for run in horse_histories[number])
                    / history_counts[index],
                    4,
                )
                if history_counts[index]
                else None
            ),
            historical_makuri_proxy_count=sum(
                int(run.get("early_corner_number") or 4) < 4
                and int(run["early_rank"]) > 3
                and int(run["fourth_rank"]) <= 3
                and int(run["early_rank"]) - int(run["fourth_rank"]) >= 3
                for run in horse_histories[number]
            ),
            historical_makuri_proxy_observations=sum(
                int(run.get("early_corner_number") or 4) < 4 for run in horse_histories[number]
            ),
            historical_makuri_proxy_rate=(
                sum(
                    int(run.get("early_corner_number") or 4) < 4
                    and int(run["early_rank"]) > 3
                    and int(run["fourth_rank"]) <= 3
                    and int(run["early_rank"]) - int(run["fourth_rank"]) >= 3
                    for run in horse_histories[number]
                )
                / sum(int(run.get("early_corner_number") or 4) < 4 for run in horse_histories[number])
                if any(int(run.get("early_corner_number") or 4) < 4 for run in horse_histories[number])
                else None
            ),
            early_expected_rank=round(early[index].expected_rank, 2),
            early_lead_probability=round(early[index].lead_probability, 4),
            early_top3_probability=round(early[index].top3_probability, 4),
            early_position_band_probabilities={
                band: round(value, 4) for band, value in early[index].band_probabilities.items()
            },
            fourth_expected_rank=round(fourth[index].expected_rank, 2),
            fourth_lead_probability=round(fourth[index].lead_probability, 4),
            fourth_top3_probability=round(fourth[index].top3_probability, 4),
            fourth_position_band_probabilities={
                band: round(value, 4) for band, value in fourth[index].band_probabilities.items()
            },
        )
        for index, number in enumerate(ordered_numbers)
    ]
    leader_indices = (
        sorted(range(field_size), key=lambda index: (-early[index].lead_probability, ordered_numbers[index]))[:3]
        if include_branches
        else []
    )
    branches: list[AlternativeLeaderScenario] = []
    for leader_index in leader_indices:
        leader_number = ordered_numbers[leader_index]
        branch_seed = seed + 100 + leader_index
        conditional = simulate_hybrid_position_distributions(
            qualities["early"],
            lead_weights["early"],
            temperature=POSITION_HYBRID_MODELS["early"].temperature,
            simulations=POSITION_SIMULATIONS,
            seed=branch_seed,
            forced_leader_index=leader_index,
        )
        branches.append(
            AlternativeLeaderScenario(
                leader_horse_number=leader_number,
                leader_horse_name=str(groups[leader_number][0]["horse_name"]),
                leader_probability=round(early[leader_index].lead_probability, 4),
                seed=branch_seed,
                simulations=POSITION_SIMULATIONS,
                horses=[
                    ConditionalEarlyPosition(
                        horse_number=number,
                        horse_name=str(groups[number][0]["horse_name"]),
                        expected_rank=round(conditional[index].expected_rank, 2),
                        top3_probability=round(conditional[index].top3_probability, 4),
                        position_band_probabilities={
                            band: round(value, 4) for band, value in conditional[index].band_probabilities.items()
                        },
                    )
                    for index, number in enumerate(ordered_numbers)
                ],
            )
        )
    front_pair_scenarios: list[FrontPairScenario] = []
    for pair_number, (first_index, second_index) in enumerate(combinations(leader_indices, 2), start=1):
        pair_seed = seed + 200 + pair_number
        conditional = simulate_hybrid_position_distributions(
            qualities["early"],
            lead_weights["early"],
            temperature=POSITION_HYBRID_MODELS["early"].temperature,
            simulations=POSITION_SIMULATIONS,
            seed=pair_seed,
            forced_front_pair=(first_index, second_index),
        )
        pair_indices = (first_index, second_index)
        front_pair_scenarios.append(
            FrontPairScenario(
                horse_numbers=[ordered_numbers[index] for index in pair_indices],
                horse_names=[str(groups[ordered_numbers[index]][0]["horse_name"]) for index in pair_indices],
                seed=pair_seed,
                simulations=POSITION_SIMULATIONS,
                horses=[
                    ConditionalEarlyPosition(
                        horse_number=number,
                        horse_name=str(groups[number][0]["horse_name"]),
                        expected_rank=round(conditional[index].expected_rank, 2),
                        top3_probability=round(conditional[index].top3_probability, 4),
                        position_band_probabilities={
                            band: round(value, 4) for band, value in conditional[index].band_probabilities.items()
                        },
                    )
                    for index, number in enumerate(ordered_numbers)
                ],
            )
        )
    lead_uncertainty_index = -sum(
        row.lead_probability * log(row.lead_probability) for row in early if row.lead_probability > 0
    ) / log(field_size)
    return horses, seed, branches, front_pair_scenarios, round(lead_uncertainty_index, 4)
