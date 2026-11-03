"""事前脚質から前半3Fの相対ペースを予測するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_date import race_date_from_code
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.pace._normalization import normalized_running_style_sql
from jvdata_bridge_mcp.tools.prediction._indicator_sql import INDICATOR_MODEL_VERSION, indicator_feature_ctes
from jvdata_bridge_mcp.tools.prediction._position_provenance import position_model_provenance
from jvdata_bridge_mcp.tools.prediction._positions_core import POSITION_MODEL_VERSION, POSITION_SIMULATIONS
from jvdata_bridge_mcp.tools.prediction.features import (
    PREDICTION_STATEMENT_TIMEOUT_SECONDS,
)
from jvdata_bridge_mcp.tools.prediction.positions import (
    AlternativeLeaderScenario,
    FrontPairScenario,
    HorsePositionScenario,
    PositionScenarioUnavailable,
    forecast_position_scenarios,
)

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class RaceScenarioItem(BaseModel):
    """脚質構成と予測ペースの1件。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    history_before_date: str | None = Field(default=None, description="過去情報の排他的な基準日。対象レース日")
    running_style_code: str | None = Field(default=None, description="直近5走の代表脚質コード")
    running_style: str = Field(description="正規化した代表脚質")
    runner_count: int = Field(description="該当馬数")
    horse_numbers: list[int] = Field(description="該当馬番")
    expected_front_runner_count: float = Field(
        description="過去の代表脚質が逃げだった頻度の縮小推定値の合計。序盤先頭確率とは別"
    )
    expected_forward_runner_count: float = Field(description="過去の代表脚質が逃げ・先行だった頻度の縮小推定値の合計")
    style_coverage_rate: float = Field(description="直近脚質履歴を1走以上持つ出走馬の割合。0〜1")
    average_first_corner_position: float | None = Field(default=None, description="直近5走の平均1コーナー順位")
    average_last_three_furlongs: float | None = Field(default=None, description="直近5走の平均上がり3ハロン。秒")
    speed_quality_score: float | None = Field(description="収録済みの馬の速度残差順位の脚質別平均。未収録はnull")
    speed_quality_coverage: float | None = Field(
        description="収録済みの馬で速度残差を得られた直近5走の割合の平均。未収録はnull"
    )
    speed_known_runner_count: int = Field(description="脚質内で速度指標を参照できた馬の数。runner_countと比較する")
    speed_baseline_level: str | None = Field(
        default=None, description="脚質別で最多の速度基準階層。基準が得られない場合はunavailable"
    )
    indicator_version: str = Field(description="共有指標設計バージョン")
    pace_pressure_index: float = Field(description="コース・距離別の実測前半3Fに対する予測標準化指数。正ほど速い")
    scenario_label: str | None = Field(default=None, description="対応範囲の相対ペース区分。対象外ならnull")


class SimulateRaceScenariosOutput(BaseModel):
    """脚質別ペースと、別モデルによる馬別コーナー位置分布。"""

    scenarios: list[RaceScenarioItem] = Field(description="脚質別の予測シナリオ")
    prediction_safe: bool = Field(
        default=True, description="対象レース日より前の履歴に限定するか。未発走の検査結果ではない"
    )
    race_code: str | None = Field(default=None, description="レースコード")
    history_before_date: str | None = Field(default=None, description="過去情報の排他的な基準日。対象レース日")
    model_version: str = Field(
        default=INDICATOR_MODEL_VERSION,
        description="相対ペースに使ったlocalモデル版",
    )
    position_model_version: str | None = Field(
        default=None,
        description="馬別コーナー位置を計算できた場合のlocalモデル版",
    )
    pace_probabilities: dict[str, float] | None = Field(
        default=None,
        description="相対ペースの確率は未校正のためnull",
    )
    simulations: int | None = Field(
        default=None,
        description="各コーナー位置の仮想順位生成回数。ペースのシミュレーション回数ではない",
    )
    seed: int | None = Field(default=None, description="馬別位置シミュレーションの再現用seed")
    horses: list[HorsePositionScenario] | None = Field(
        default=None,
        description="馬別の最初に記録されたコーナーと4コーナーの位置分布",
    )
    leader_branches: list[AlternativeLeaderScenario] | None = Field(
        default=None,
        description="最初の記録コーナーで先頭と仮定する上位3候補の条件付き隊列。4コーナーやペースの条件付き予測ではない",
    )
    front_pair_scenarios: list[FrontPairScenario] | None = Field(
        default=None,
        description="先頭候補上位3頭から二頭ずつを前2頭に固定した序盤隊列。競り合いの発生確率やペース変化は未推定",
    )
    other_leaders_probability: float | None = Field(
        default=None, description="上位3候補以外が最初の記録コーナーで先頭になる確率の合計"
    )
    lead_uncertainty_index: float | None = Field(
        default=None, description="先頭候補確率の分散度。0〜1。競り合いによるペース上昇確率ではない"
    )
    position_unavailable_reason: str | None = Field(
        default=None,
        description="馬別位置が検証範囲外で計算されなかった理由",
    )
    output_granularity: str = Field(
        default="style_aggregate",
        description="脚質別ペースのみ、または馬別位置を含む粒度",
    )
    probability_status: str = Field(
        default="not_computed",
        description="馬別位置だけ確率を計算した状態、または検証範囲外で未計算の状態",
    )
    simulation_status: str = Field(
        default="not_run",
        description="馬別コーナー位置の仮想順位生成の実行状態",
    )
    calibration_status: str = "position_simulation_not_empirically_calibrated; development_exposure_2025_2026"
    position_model_provenance: dict[str, object] = Field(default_factory=position_model_provenance)
    pace_input_semantics: str = "representative_running_style_and_corner1; not_early_leader_or_makuri_probability"
    pace_position_consistency: str = "separate_marginals; joint_tactical_and_pace_consistency_not_validated"


_HISTORICAL_CUTOFF_DATE = "r.race_date"
_NORMALIZED_RUNNING_STYLE = normalized_running_style_sql("recent_history.running_style_code")
_FEATURE_CTES = indicator_feature_ctes(_HISTORICAL_CUTOFF_DATE)

_SQL = f"""
{_FEATURE_CTES}, horse_tendencies AS MATERIALIZED (
    SELECT
        c.horse_number,
        COALESCE(history.dominant_running_style, 'unknown') AS running_style,
        COALESCE(history.front_probability, 0.10) AS front_probability,
        COALESCE(history.forward_probability, 0.34) AS forward_probability,
        COALESCE(history.first_corner_quality, 0.50) AS first_corner_quality,
        COALESCE(history.has_style_history, FALSE) AS has_style_history,
        history.average_first_corner_position,
        history.average_last_three_furlongs,
        indicator.speed_quality_score,
        indicator.speed_quality_coverage_raw AS speed_quality_coverage,
        indicator.speed_baseline_level
    FROM target_cards AS c
    LEFT JOIN component_scores AS indicator ON indicator.horse_id = c.horse_id
    LEFT JOIN LATERAL (
        SELECT
            MODE() WITHIN GROUP (ORDER BY styled.running_style)
                FILTER (WHERE styled.running_style <> 'unknown') AS dominant_running_style,
            (
                COALESCE(SUM(styled.is_front * styled.recency_weight)
                    FILTER (WHERE styled.running_style <> 'unknown'), 0.0) + 0.20
            ) / (
                COALESCE(SUM(styled.recency_weight)
                    FILTER (WHERE styled.running_style <> 'unknown'), 0.0) + 2.0
            ) AS front_probability,
            (
                COALESCE(SUM(styled.is_forward * styled.recency_weight)
                    FILTER (WHERE styled.running_style <> 'unknown'), 0.0) + 0.68
            ) / (
                COALESCE(SUM(styled.recency_weight)
                    FILTER (WHERE styled.running_style <> 'unknown'), 0.0) + 2.0
            ) AS forward_probability,
            (
                COALESCE(SUM(styled.first_corner_quality * styled.recency_weight)
                    FILTER (WHERE styled.first_corner_quality IS NOT NULL), 0.0) + 1.0
            ) / (
                COALESCE(SUM(styled.recency_weight)
                    FILTER (WHERE styled.first_corner_quality IS NOT NULL), 0.0) + 2.0
            ) AS first_corner_quality,
            COUNT(*) FILTER (WHERE styled.running_style <> 'unknown') > 0 AS has_style_history,
            AVG(styled.corner_1_position) AS average_first_corner_position,
            AVG(styled.last_three_furlongs_seconds) AS average_last_three_furlongs
        FROM (
            SELECT
                recent_history.*,
                {_NORMALIZED_RUNNING_STYLE} AS running_style,
                CASE
                    WHEN {_NORMALIZED_RUNNING_STYLE} = 'front_runner' THEN 1.0
                    ELSE 0.0
                END AS is_front,
                CASE
                    WHEN {_NORMALIZED_RUNNING_STYLE} IN ('front_runner', 'stalker') THEN 1.0
                    ELSE 0.0
                END AS is_forward,
                CASE recent_history.recency_rank
                    WHEN 1 THEN 1.0
                    WHEN 2 THEN 0.72
                    WHEN 3 THEN 0.5184
                    WHEN 4 THEN 0.373248
                    ELSE 0.26873856
                END AS recency_weight,
                CASE
                    WHEN recent_history.corner_1_position IS NULL THEN NULL
                    ELSE (recent_history.starters - recent_history.corner_1_position)::double precision
                        / NULLIF(recent_history.starters - 1, 0)
                END AS first_corner_quality
            FROM (
                SELECT
                    h.running_style_code,
                    h.corner_1_position,
                    h.last_three_furlongs_seconds,
                    history_race.starters,
                    ROW_NUMBER() OVER (ORDER BY h.race_date DESC, h.race_id DESC) AS recency_rank
                FROM jv_mart.horse_runs AS h
                LEFT JOIN LATERAL (
                    SELECT r.starters
                    FROM jv_data.races AS r
                    WHERE r.race_id = h.race_id
                    LIMIT 1
                ) AS history_race ON TRUE
                WHERE h.horse_id = c.horse_id
                  AND h.race_date < c.historical_cutoff_date
                  AND h.is_final_result
                  AND h.is_complete_result
                  AND h.is_starter
                  AND h.is_finisher
                ORDER BY h.race_date DESC, h.race_id DESC
                LIMIT 5
            ) AS recent_history
        ) AS styled
    ) AS history ON TRUE
), field_summary AS MATERIALIZED (
    SELECT
        COUNT(*)::bigint AS field_size,
        SUM(front_probability) AS expected_front_runner_count,
        SUM(forward_probability) AS expected_forward_runner_count,
        AVG(first_corner_quality) AS average_first_corner_quality,
        COUNT(*) FILTER (WHERE has_style_history)::double precision / NULLIF(COUNT(*), 0) AS style_coverage_rate
    FROM horse_tendencies
), predicted AS MATERIALIZED (
    SELECT
        f.*,
        -3.772402
        + 1.513297 * (f.expected_front_runner_count / NULLIF(f.field_size, 0))
        + 1.803313 * (f.expected_forward_runner_count / NULLIF(f.field_size, 0))
        + 1.783288 * f.average_first_corner_quality
        + 1.153411 * f.style_coverage_rate
        + 1.474942 * (f.field_size / 18.0) AS pace_pressure_index
    FROM field_summary AS f
)
SELECT
    :race_code AS race_code,
    (SELECT race_date FROM target) AS history_before_date,
    h.running_style AS running_style_code,
    h.running_style,
    COUNT(*) AS runner_count,
    ARRAY_AGG(h.horse_number ORDER BY h.horse_number) AS horse_numbers,
    ROUND(p.expected_front_runner_count::numeric, 3) AS expected_front_runner_count,
    ROUND(p.expected_forward_runner_count::numeric, 3) AS expected_forward_runner_count,
    ROUND(p.style_coverage_rate::numeric, 4) AS style_coverage_rate,
    ROUND(AVG(h.average_first_corner_position)::numeric, 3) AS average_first_corner_position,
    ROUND(AVG(h.average_last_three_furlongs)::numeric, 3) AS average_last_three_furlongs,
    ROUND(AVG(h.speed_quality_score)::numeric, 4) AS speed_quality_score,
    ROUND(AVG(h.speed_quality_coverage)::numeric, 4) AS speed_quality_coverage,
    COUNT(h.speed_quality_score) AS speed_known_runner_count,
    MODE() WITHIN GROUP (ORDER BY h.speed_baseline_level)
        FILTER (WHERE h.speed_baseline_level IS NOT NULL) AS speed_baseline_level,
    '{INDICATOR_MODEL_VERSION}' AS indicator_version,
    ROUND(p.pace_pressure_index::numeric, 4) AS pace_pressure_index,
    CASE
        WHEN p.pace_pressure_index >= 0.5 THEN 'fast_pace'
        WHEN p.pace_pressure_index <= -0.5 THEN 'slow_pace'
        ELSE 'balanced_pace'
    END AS scenario_label
FROM horse_tendencies AS h
CROSS JOIN predicted AS p
GROUP BY
    h.running_style,
    p.expected_front_runner_count,
    p.expected_forward_runner_count,
    p.style_coverage_rate,
    p.pace_pressure_index
ORDER BY runner_count DESC, h.running_style
LIMIT :limit
"""


def build_simulate_race_scenarios_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したsimulate_race_scenarios関数を作る。"""

    async def simulate_race_scenarios(
        race_code: RaceCode,
    ) -> SimulateRaceScenariosOutput:
        """前半3Fの相対ペースと、馬別のコーナー位置分布を予測する。

        各馬の過去の代表脚質が逃げ・先行だった頻度、過去1コーナー順位、履歴被覆率、頭数を使う線形モデルである。
        代表脚質の逃げを序盤先頭や、途中で進出するまくりと同一視しない。出力には
        共有v3速度指標の脚質別平均・被覆率・基準階層も含めるが、ペース係数自体へ速度を加算しない。
        ペース係数の学習期間は2021〜2024年、開発評価期間は2025年である。
        馬別位置は馬の過去20走、騎手の過去50騎乗、馬番・距離・競馬場から
        線形モデルで推定する。先頭は別の分類器で計算し、残りの位置を
        分布として生成する。位置帯の確率と、先頭一頭または上位二頭を固定した
        場合の序盤隊列も返す。
        条件付き分岐は最初の記録コーナーの感度分析で、ペース上昇や4コーナーの
        条件付き変化は推定しない。位置モデルの学習期間は2012〜2024年、設定選択期間は
        2023〜2024年、開発で参照済みの評価期間は2025〜2026年である。
        詳細な適用域と較正上の制約はposition_model_provenanceに含める。
        対象日と同日の結果や対象レースのオッズは使わない。
        位置分布は8〜18頭の芝・ダート平地戦のみ対応する。

        Args:
            race_code: 対象レースのレースコード。

        Returns:
            脚質構成、連続ペース指数、馬別の最初に記録されたコーナーと
            4コーナーの期待順位・先頭確率・前3頭確率・位置帯確率と、
            上位3頭の先頭候補を仮定した最初の記録コーナーの隊列と、
            二頭が前方にいる場合の代替隊列。
            相対ペース確率は未校正のためnullで返す。
        """

        target_date = race_date_from_code(race_code)
        rows = await fetch_rows(
            database,
            _SQL,
            {"race_code": race_code, "limit": 100},
            statement_timeout_seconds=PREDICTION_STATEMENT_TIMEOUT_SECONDS,
        )
        try:
            (
                horses,
                seed,
                leader_branches,
                front_pair_scenarios,
                lead_uncertainty_index,
            ) = await forecast_position_scenarios(database, race_code)
            position_unavailable_reason = None
        except PositionScenarioUnavailable as exc:
            horses, seed = None, None
            leader_branches, front_pair_scenarios, lead_uncertainty_index = None, None, None
            position_unavailable_reason = str(exc)
        for row in rows:
            if position_unavailable_reason:
                row["scenario_label"] = None
        return SimulateRaceScenariosOutput(
            scenarios=rows,
            prediction_safe=True,
            race_code=race_code,
            history_before_date=target_date.isoformat(),
            model_version=INDICATOR_MODEL_VERSION,
            position_model_version=POSITION_MODEL_VERSION if horses is not None else None,
            pace_probabilities=None,
            simulations=POSITION_SIMULATIONS if horses is not None else None,
            seed=seed,
            horses=horses,
            leader_branches=leader_branches,
            front_pair_scenarios=front_pair_scenarios,
            other_leaders_probability=(
                round(max(0.0, 1.0 - sum(branch.leader_probability for branch in leader_branches)), 4)
                if leader_branches is not None
                else None
            ),
            lead_uncertainty_index=lead_uncertainty_index,
            position_unavailable_reason=position_unavailable_reason,
            output_granularity="style_and_horse_position" if horses is not None else "style_aggregate",
            probability_status="horse_positions_only" if horses is not None else "not_computed",
            simulation_status="horse_positions_run" if horses is not None else "unsupported_race",
        )

    return simulate_race_scenarios
