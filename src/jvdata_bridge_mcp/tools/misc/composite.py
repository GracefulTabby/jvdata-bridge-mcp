"""時系列検証済みの複数指標を合成するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
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
IndicatorWeight = Annotated[float, Field(description="バックテスト済み指標へ掛ける非負の重み")]


class CompositeAnalysisItem(BaseModel):
    """事前履歴から作った出走馬別の総合分析1件。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: int | None = Field(default=None, description="馬番")
    horse_id: str = Field(description="競走馬ID")
    horse_name: str = Field(description="馬名")
    jockey_id: str | None = Field(default=None, description="騎手ID")
    trainer_id: str | None = Field(default=None, description="調教師ID")
    average_finish_position: float | None = Field(default=None, description="対象日より前の平均着順")
    historical_starts: int = Field(description="対象日より前の出走数")
    historical_top3: int = Field(description="対象日より前の3着以内数")
    average_last_three_furlongs: float | None = Field(default=None, description="参考用の過去平均上がり3F。秒")
    average_win_odds: float | None = Field(default=None, description="参考用の過去平均単勝オッズ")
    assigned_weight_kg: float | None = Field(default=None, description="負担重量。能力点には直接加算しない")
    history_reliability: float = Field(description="過去走数による0〜1の情報信頼度。能力点ではない")
    aptitude_score: float = Field(description="ベイズ平滑化した複勝実績の同レース内0〜1順位")
    recent_form_score: float = Field(description="直近5走の着順品質を時間減衰した同レース内0〜1順位")
    distance_aptitude_score: float = Field(description="同じ馬場系統・距離帯実績の同レース内0〜1順位")
    course_aptitude_score: float = Field(description="同じ競馬場・馬場系統実績の同レース内0〜1順位")
    closing_score: float = Field(description="過去走ごとに出走馬間正規化した末脚の同レース内0〜1順位")
    time_quality_score: float = Field(description="直近の勝馬タイム差を減衰変換した同レース内0〜1順位")
    speed_quality_score: float = Field(description="過去時計の階層基準から作った速度残差の同レース内0〜1順位")
    speed_quality_coverage: float = Field(description="速度残差を得られた直近5走の割合。0〜1")
    speed_baseline_level: str | None = Field(
        default=None, description="直近の速度残差で選んだ基準階層。履歴不足はunavailable"
    )
    historical_market_score: float = Field(description="過去人気を頭数補正した同レース内0〜1順位")
    jockey_form_score: float = Field(description="騎手の事前成績を縮小推定した同レース内0〜1順位")
    trainer_form_score: float = Field(description="調教師の事前成績を縮小推定した同レース内0〜1順位")
    horse_jockey_form_score: float = Field(description="馬・騎手の事前成績を縮小推定した同レース内0〜1順位")
    composite_score: float = Field(description="過去市場支持を含む総合比較スコア")
    market_free_composite_score: float = Field(description="過去市場支持を除いた市場なし診断用総合比較スコア")
    market_support_utility: float = Field(description="過去市場支持が市場あり総合効用へ加えた値")
    market_support_delta: float = Field(description="市場あり総合点から市場なし総合点を引いた差分")
    indicator_version: str = Field(description="指標設計バージョン")
    market_free_model_version: str = Field(description="市場なし診断モデルのバージョン")
    market_free_model_description: str = Field(
        description="市場なし診断モデルの説明。独立再学習・再校正済みではないことを含む"
    )
    selected_race_code: str = Field(description="対象レースコード")


class GetCompositeAnalysisOutput(BaseModel):
    """時系列検証済みの出走馬別総合分析。"""

    horses: list[CompositeAnalysisItem] = Field(description="出走馬別の総合分析")


_FEATURE_CTES = indicator_feature_ctes("r.race_date")

_SQL = f"""
{_FEATURE_CTES}, scored AS MATERIALIZED (
    SELECT
        s.*,
        s.aptitude_score * :aptitude_weight
        + s.recent_form_score * :recent_form_weight
        + s.distance_aptitude_score * :distance_aptitude_weight
        + s.course_aptitude_score * :course_aptitude_weight
        + s.closing_score * :closing_weight
        + s.time_quality_score * :time_quality_weight
        + s.speed_quality_score * :speed_quality_weight
        + s.historical_market_score * :historical_market_weight
        + s.jockey_form_score * :jockey_form_weight
        + s.trainer_form_score * :trainer_form_weight
        + s.horse_jockey_form_score * :horse_jockey_form_weight AS composite_score,
        s.aptitude_score * :aptitude_weight
        + s.recent_form_score * :recent_form_weight
        + s.distance_aptitude_score * :distance_aptitude_weight
        + s.course_aptitude_score * :course_aptitude_weight
        + s.closing_score * :closing_weight
        + s.time_quality_score * :time_quality_weight
        + s.speed_quality_score * :speed_quality_weight
        + s.jockey_form_score * :jockey_form_weight
        + s.trainer_form_score * :trainer_form_weight
        + s.horse_jockey_form_score * :horse_jockey_form_weight AS market_free_composite_score,
        s.historical_market_score * :historical_market_weight AS market_support_utility
    FROM component_scores AS s
)
SELECT
    horse_number,
    horse_id,
    horse_name,
    jockey_id,
    trainer_id,
    average_finish_position,
    starts AS historical_starts,
    top3_finishes AS historical_top3,
    average_last_three_furlongs,
    average_win_odds,
    assigned_weight_kg,
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
    composite_score,
    market_free_composite_score,
    market_support_utility,
    composite_score - market_free_composite_score AS market_support_delta,
    '{INDICATOR_MODEL_VERSION}' AS indicator_version,
    '{MARKET_FREE_MODEL_VERSION}' AS market_free_model_version,
    '{MARKET_FREE_MODEL_DESCRIPTION}' AS market_free_model_description,
    :race_code AS selected_race_code
FROM scored
ORDER BY composite_score DESC, horse_number
LIMIT :limit
"""


def build_get_composite_analysis_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したget_composite_analysis関数を作る。"""

    async def get_composite_analysis(
        race_code: RaceCode,
        aptitude_weight: IndicatorWeight = DEFAULT_INDICATOR_WEIGHTS["aptitude_weight"],
        recent_form_weight: IndicatorWeight = DEFAULT_INDICATOR_WEIGHTS["recent_form_weight"],
        distance_aptitude_weight: IndicatorWeight = DEFAULT_INDICATOR_WEIGHTS["distance_aptitude_weight"],
        course_aptitude_weight: IndicatorWeight = DEFAULT_INDICATOR_WEIGHTS["course_aptitude_weight"],
        closing_weight: IndicatorWeight = DEFAULT_INDICATOR_WEIGHTS["closing_weight"],
        time_quality_weight: IndicatorWeight = DEFAULT_INDICATOR_WEIGHTS["time_quality_weight"],
        historical_market_weight: IndicatorWeight = DEFAULT_INDICATOR_WEIGHTS["historical_market_weight"],
        jockey_form_weight: IndicatorWeight = DEFAULT_INDICATOR_WEIGHTS["jockey_form_weight"],
        trainer_form_weight: IndicatorWeight = DEFAULT_INDICATOR_WEIGHTS["trainer_form_weight"],
        horse_jockey_form_weight: IndicatorWeight = DEFAULT_INDICATOR_WEIGHTS["horse_jockey_form_weight"],
        speed_quality_weight: IndicatorWeight = SPEED_QUALITY_WEIGHT,
    ) -> GetCompositeAnalysisOutput:
        """事前情報だけで作った比較可能な指標を重み付けし、出走馬を順位付けする。

        軽斤量や出走回数を能力として直接加点せず、近走、条件適性、末脚、タイム差、
        過去市場評価、速度残差、騎手・調教師・馬騎手の縮小推定成績を合成する。
        既定重みの学習期間は2021〜2025年、設定選択期間は2023〜2024年である。
        2026年は開発で参照済みの評価期間であり、未見の最終評価期間ではない。
        ``market_free_composite_score`` は同じ係数から ``historical_market`` 項だけを除いた
        市場項の寄与を調べる診断値であり、独立再学習・再校正済みの総合点ではない。

        Args:
            race_code: 対象レースのレースコード。
            aptitude_weight: ベイズ平滑化した複勝実績の重み。
            recent_form_weight: 直近5走の時間減衰成績の重み。
            distance_aptitude_weight: 馬場系統・距離帯適性の重み。
            course_aptitude_weight: 競馬場・馬場系統適性の重み。
            closing_weight: 過去レース内で正規化した末脚の重み。
            time_quality_weight: 勝馬タイム差を減衰変換した指標の重み。
            historical_market_weight: 過去人気を頭数補正した指標の重み。
            jockey_form_weight: 騎手の縮小推定成績の重み。
            trainer_form_weight: 調教師の縮小推定成績の重み。
            horse_jockey_form_weight: 馬・騎手の縮小推定成績の重み。
            speed_quality_weight: 過去時計の階層速度残差の重み。

        Returns:
            出走馬別の総合比較指標。
        """

        weights = {
            "aptitude_weight": max(0.0, aptitude_weight),
            "recent_form_weight": max(0.0, recent_form_weight),
            "distance_aptitude_weight": max(0.0, distance_aptitude_weight),
            "course_aptitude_weight": max(0.0, course_aptitude_weight),
            "closing_weight": max(0.0, closing_weight),
            "time_quality_weight": max(0.0, time_quality_weight),
            "historical_market_weight": max(0.0, historical_market_weight),
            "jockey_form_weight": max(0.0, jockey_form_weight),
            "trainer_form_weight": max(0.0, trainer_form_weight),
            "horse_jockey_form_weight": max(0.0, horse_jockey_form_weight),
            "speed_quality_weight": max(0.0, speed_quality_weight),
        }
        await ensure_speed_quality_available(database, race_code)
        rows = await fetch_rows(
            database,
            _SQL,
            {"race_code": race_code, **weights, "limit": 100},
            statement_timeout_seconds=PREDICTION_STATEMENT_TIMEOUT_SECONDS,
        )
        return GetCompositeAnalysisOutput(horses=rows)

    return get_composite_analysis
