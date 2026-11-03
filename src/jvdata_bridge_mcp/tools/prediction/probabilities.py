"""レースの予測確率を直接SQLで計算するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.ml.artifacts import load_active_model
from jvdata_bridge_mcp.ml.distribution import finish_distribution, ticket_rows
from jvdata_bridge_mcp.tools._race_date import race_date_from_code
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.prediction._indicator_sql import (
    DEFAULT_INDICATOR_WEIGHTS,
    INDICATOR_MODEL_VERSION,
    MARKET_FREE_MODEL_DESCRIPTION,
    MARKET_FREE_MODEL_VERSION,
    SPEED_QUALITY_WEIGHT,
    indicator_feature_ctes,
)
from jvdata_bridge_mcp.tools.prediction._speed_read_model import ensure_speed_quality_available
from jvdata_bridge_mcp.tools.prediction.features import (
    PREDICTION_STATEMENT_TIMEOUT_SECONDS,
)

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class HistoricalMarketDiagnostics(BaseModel):
    """同梱線形モデルから過去の市場支持項を除いた診断値。"""

    model_config = ConfigDict(extra="forbid")

    model_version: str
    description: str
    raw_score: float
    utility: float
    probability: float
    support_utility: float
    support_delta: float


class RaceProbabilityItem(BaseModel):
    """出走馬ごとの確率計算結果1件。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    history_before_date: str | None = Field(default=None, description="過去情報の排他的な基準日。対象レース日")
    model_version: str | None = Field(default=None, description="モデルバージョン")
    horse_number: int | None = Field(default=None, description="馬番")
    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    starts: int = Field(description="過去出走数")
    average_finish_position: float | None = Field(default=None, description="平均着順")
    average_win_odds: float | None = Field(default=None, description="平均単勝オッズ")
    average_last_three_furlongs: float | None = Field(default=None, description="平均上がり3ハロン。秒")
    history_reliability: float = Field(description="過去走数に基づく0〜1の情報信頼度。能力点には加算しない")
    aptitude_score: float = Field(description="ベイズ平滑化した複勝実績の同レース内0〜1順位")
    recent_form_score: float = Field(description="直近5走の着順品質を時間減衰した同レース内0〜1順位")
    distance_aptitude_score: float = Field(description="同じ馬場系統・距離帯実績の同レース内0〜1順位")
    course_aptitude_score: float = Field(description="同じ競馬場・馬場系統実績の同レース内0〜1順位")
    closing_score: float = Field(description="過去走ごとに出走馬間正規化した末脚の同レース内0〜1順位")
    time_quality_score: float = Field(description="直近の勝馬タイム差を減衰変換した同レース内0〜1順位")
    speed_quality_score: float | None = Field(
        description="過去時計の階層基準から作った速度残差の同レース内0〜1順位。欠測対応モデルではnull可"
    )
    speed_quality_coverage: float | None = Field(description="速度残差を得られた直近5走の割合。0〜1。未収録はnull")
    speed_baseline_level: str | None = Field(
        default=None, description="直近の速度残差で選んだ基準階層。履歴不足はunavailable"
    )
    historical_market_score: float | None = Field(
        description="過去人気を頭数補正した同レース内0〜1順位。市場なし採用モデルでは未計算"
    )
    jockey_form_score: float = Field(description="騎手の事前成績を縮小推定した同レース内0〜1順位")
    trainer_form_score: float = Field(description="調教師の事前成績を縮小推定した同レース内0〜1順位")
    horse_jockey_form_score: float = Field(description="馬・騎手の事前成績を縮小推定した同レース内0〜1順位")
    raw_score: float = Field(description="採用モデルの正の生スコア。線形モデルでは効用の指数")
    win_probability: float = Field(description="実際に採用されたモデルの勝率。版と較正状態を併せて確認")
    top2_probability: float | None = Field(default=None, description="同じ着順分布から計算した2着以内率")
    top3_probability: float | None = Field(
        default=None, description="同じ着順分布から計算した3着以内率。複勝発売条件とは区別"
    )
    historical_market_diagnostics: HistoricalMarketDiagnostics | None = Field(
        default=None,
        description="線形モデルの過去市場支持を除いた診断値。独立学習・較正はしていない。市場なし採用モデルではnull",
    )
    prediction_source: str | None = Field(default=None, description="予測ソース")


class PredictRaceProbabilitiesOutput(BaseModel):
    """出走馬ごとの確率計算結果。"""

    probabilities: list[RaceProbabilityItem] = Field(description="出走馬ごとの予測確率")
    model_version: str | None = None
    calibration_status: str = "linear_softmax_not_empirically_calibrated"
    combination_status: str = "research_reference; strict_order_distribution_not_a_profit_claim"
    combination_probabilities: list[dict[str, object]] = Field(default_factory=list)
    distribution_checks: dict[str, object] = Field(default_factory=dict)
    provenance: dict[str, object] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


_HISTORICAL_CUTOFF_DATE = "r.race_date"
_FEATURE_CTES = indicator_feature_ctes(_HISTORICAL_CUTOFF_DATE)


_SQL = f"""
{_FEATURE_CTES}, scored AS MATERIALIZED (
    SELECT
        c.*,
        c.aptitude_score * {DEFAULT_INDICATOR_WEIGHTS["aptitude_weight"]}
        + c.recent_form_score * {DEFAULT_INDICATOR_WEIGHTS["recent_form_weight"]}
        + c.distance_aptitude_score * {DEFAULT_INDICATOR_WEIGHTS["distance_aptitude_weight"]}
        + c.course_aptitude_score * {DEFAULT_INDICATOR_WEIGHTS["course_aptitude_weight"]}
        + c.closing_score * {DEFAULT_INDICATOR_WEIGHTS["closing_weight"]}
        + c.time_quality_score * {DEFAULT_INDICATOR_WEIGHTS["time_quality_weight"]}
        + c.speed_quality_score * {SPEED_QUALITY_WEIGHT}
        + c.historical_market_score * {DEFAULT_INDICATOR_WEIGHTS["historical_market_weight"]}
        + c.jockey_form_score * {DEFAULT_INDICATOR_WEIGHTS["jockey_form_weight"]}
        + c.trainer_form_score * {DEFAULT_INDICATOR_WEIGHTS["trainer_form_weight"]}
        + c.horse_jockey_form_score * {DEFAULT_INDICATOR_WEIGHTS["horse_jockey_form_weight"]} AS model_utility,
        c.aptitude_score * {DEFAULT_INDICATOR_WEIGHTS["aptitude_weight"]}
        + c.recent_form_score * {DEFAULT_INDICATOR_WEIGHTS["recent_form_weight"]}
        + c.distance_aptitude_score * {DEFAULT_INDICATOR_WEIGHTS["distance_aptitude_weight"]}
        + c.course_aptitude_score * {DEFAULT_INDICATOR_WEIGHTS["course_aptitude_weight"]}
        + c.closing_score * {DEFAULT_INDICATOR_WEIGHTS["closing_weight"]}
        + c.time_quality_score * {DEFAULT_INDICATOR_WEIGHTS["time_quality_weight"]}
        + c.speed_quality_score * {SPEED_QUALITY_WEIGHT}
        + c.jockey_form_score * {DEFAULT_INDICATOR_WEIGHTS["jockey_form_weight"]}
        + c.trainer_form_score * {DEFAULT_INDICATOR_WEIGHTS["trainer_form_weight"]}
        + c.horse_jockey_form_score * {DEFAULT_INDICATOR_WEIGHTS["horse_jockey_form_weight"]} AS market_free_utility,
        c.historical_market_score * {DEFAULT_INDICATOR_WEIGHTS["historical_market_weight"]} AS market_support_utility
    FROM component_scores AS c
), normalized AS MATERIALIZED (
    SELECT
        s.*,
        EXP(s.model_utility) AS raw_score,
        EXP(s.market_free_utility) AS market_free_raw_score,
        SUM(EXP(s.model_utility)) OVER () AS total_raw_score,
        SUM(EXP(s.market_free_utility)) OVER () AS total_market_free_raw_score
    FROM scored AS s
)
SELECT
    :race_code AS race_code,
    historical_cutoff_date AS history_before_date,
    '{INDICATOR_MODEL_VERSION}' AS model_version,
    '{MARKET_FREE_MODEL_VERSION}' AS market_free_model_version,
    '{MARKET_FREE_MODEL_DESCRIPTION}' AS market_free_model_description,
    horse_number,
    horse_id,
    horse_name,
    starts,
    average_finish_position,
    average_win_odds,
    average_last_three_furlongs,
    history_reliability_raw AS history_reliability,
    aptitude_score,
    recent_form_score,
    distance_aptitude_score,
    course_aptitude_score,
    closing_score,
    time_quality_score,
    speed_quality_score,
    speed_quality_coverage_raw AS speed_quality_coverage,
    speed_baseline_level,
    historical_market_score,
    jockey_form_score,
    trainer_form_score,
    horse_jockey_form_score,
    raw_score,
    market_free_raw_score,
    market_free_utility,
    market_support_utility,
    raw_score / NULLIF(total_raw_score, 0) AS win_probability,
    market_free_raw_score / NULLIF(total_market_free_raw_score, 0) AS market_free_probability,
    CASE
        WHEN starts > 0 THEN 'temporal_linear_v3_speed'
        ELSE 'temporal_linear_v3_speed_horse_prior'
    END AS prediction_source
FROM normalized
ORDER BY win_probability DESC, horse_number
LIMIT :limit
"""


def build_predict_race_probabilities_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したpredict_race_probabilities関数を作る。"""

    async def predict_race_probabilities(
        race_code: RaceCode,
        combinations: Annotated[
            dict[str, list[str]] | None,
            Field(
                description="必要時だけ指定。quinella/wide/trio/trifectaの組合せ計200件以内。省略は券種ごと上位20件"
            ),
        ] = None,
    ) -> PredictRaceProbabilitiesOutput:
        """対象レースの出走馬ごとの予測確率を取得する。

        採用基準を通過したJSONがあれば、その市場なしモデルを使う。JSONがなければ、2021〜2025年で
        学習した11指標の線形係数を使う。実際の版・学習期間・較正状態は出力のモデル情報で確認する。
        特徴量は対象レースの前日までに限定し、対象日以降の結果や対象の現在オッズを入力にしない。
        線形モデルのhistorical_market_diagnosticsは過去市場項だけを除いた診断値で、独立した再学習モデルではない。
        Softmaxだけで較正済みとは扱わない。
        同じ着順分布から勝率・2着内・3着内と馬連・ワイド・3連複・3連単を返す。
        ワイドは複数同時的中するため的中確率の総和が3になる。較正状態と適用域を併せて確認する。

        Args:
            race_code: 対象レースのレースコード。
            combinations: 必要時だけ券種と組合せを指定する。省略時は券種ごとに上位20件。

        Returns:
            出走馬と組合せの確率、較正状態、情報境界とモデルの出典。
        """

        race_date_from_code(race_code)
        active_model = load_active_model()
        if active_model is not None:
            from jvdata_bridge_mcp.ml.prediction import ability_prediction

            return PredictRaceProbabilitiesOutput(
                **await ability_prediction(database, race_code, active_model, combinations)
            )
        from jvdata_bridge_mcp.ml.live import current_roster

        cards, boundary, observations = await current_roster(database, race_code)
        await ensure_speed_quality_available(database, race_code)
        rows = await fetch_rows(
            database,
            _SQL,
            {
                "race_code": race_code,
                "limit": 100,
            },
            statement_timeout_seconds=PREDICTION_STATEMENT_TIMEOUT_SECONDS,
        )
        if not rows:
            return PredictRaceProbabilitiesOutput(probabilities=[])
        active_numbers = {int(c["horse_number"]) for c in cards}
        rows = [r for r in rows if int(r["horse_number"]) in active_numbers]
        if not rows:
            raise ValueError("no active runners after confirmed withdrawal notices")
        for field in ("win_probability", "market_free_probability"):
            total = sum(float(r[field]) for r in rows)
            for row in rows:
                row[field] = float(row[field]) / total
        for row in rows:
            probability = float(row.pop("market_free_probability"))
            row["historical_market_diagnostics"] = {
                "model_version": row.pop("market_free_model_version"),
                "description": row.pop("market_free_model_description"),
                "raw_score": row.pop("market_free_raw_score"),
                "utility": row.pop("market_free_utility"),
                "probability": probability,
                "support_utility": row.pop("market_support_utility"),
                "support_delta": float(row["win_probability"]) - probability,
            }
        distribution = finish_distribution(
            [int(r["horse_number"]) for r in rows], [float(r["win_probability"]) for r in rows]
        )
        for row in rows:
            marginal = distribution["marginals"][int(row["horse_number"])]
            row["top2_probability"], row["top3_probability"] = (
                marginal["top2_probability"],
                marginal["top3_probability"],
            )
        return PredictRaceProbabilitiesOutput(
            probabilities=rows,
            model_version=INDICATOR_MODEL_VERSION,
            combination_probabilities=ticket_rows(distribution, combinations),
            distribution_checks={
                "joint_probability_sum": distribution["joint_probability_sum"],
                "ticket_probability_sums": distribution["ticket_probability_sums"],
            },
            provenance={"boundary": boundary.model_dump(), "current_entry_observations": observations.model_dump()},
            limitations=distribution["assumptions"]
            + ["Bundled linear win and combination probabilities require independent empirical calibration."],
        )

    return predict_race_probabilities
