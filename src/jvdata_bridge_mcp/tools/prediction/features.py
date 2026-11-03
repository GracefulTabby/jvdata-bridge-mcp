"""予測用の馬特徴量を直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable, Final

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.ml.artifacts import load_active_model
from jvdata_bridge_mcp.tools._race_date import race_date_from_code
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.prediction._indicator_sql import (
    INDICATOR_MODEL_VERSION,
    indicator_feature_ctes,
)
from jvdata_bridge_mcp.tools.prediction._speed_read_model import ensure_speed_quality_available

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]

PREDICTION_STATEMENT_TIMEOUT_SECONDS: Final[int] = 60


class HorseFeatureItem(BaseModel):
    """出走馬ごとの予測特徴量1件。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    history_before_date: str | None = Field(default=None, description="過去情報の排他的な基準日。対象レース日")
    horse_number: int | None = Field(default=None, description="馬番")
    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    historical_starts: int = Field(description="過去出走数")
    historical_wins: int = Field(description="過去勝利数")
    historical_top3: int = Field(description="過去3着以内数")
    average_finish_position: float | None = Field(default=None, description="平均着順")
    average_win_odds: float | None = Field(default=None, description="平均単勝オッズ")
    average_last_three_furlongs: float | None = Field(default=None, description="平均上がり3ハロン。秒")
    aptitude_score: float = Field(description="複勝実績の同レース内0〜1順位")
    recent_form_score: float = Field(description="直近5走の着順品質の同レース内0〜1順位")
    distance_aptitude_score: float = Field(description="距離適性の同レース内0〜1順位")
    course_aptitude_score: float = Field(description="競馬場適性の同レース内0〜1順位")
    closing_score: float = Field(description="末脚の同レース内0〜1順位")
    time_quality_score: float = Field(description="タイム差品質の同レース内0〜1順位")
    speed_quality_score: float | None = Field(
        description="過去時計の速度残差の同レース内0〜1順位。欠測対応モデルではnull可"
    )
    speed_quality_coverage: float | None = Field(description="速度残差を得られた直近5走の割合。0〜1。未収録はnull")
    speed_baseline_level: str | None = Field(default=None, description="速度基準の選択階層。履歴不足はunavailable")
    indicator_version: str = Field(description="指標設計バージョン")
    has_history: bool = Field(description="過去成績が存在するか")


class GetHorseFeatureMatrixOutput(BaseModel):
    """出走馬ごとの予測特徴量行。"""

    features: list[HorseFeatureItem] = Field(description="出走馬ごとの予測特徴量")
    ability_features: list[dict[str, object]] = Field(
        default_factory=list, description="採用済みモデルの市場なし特徴量。欠測はnull"
    )
    model_provenance: dict[str, object] = Field(default_factory=dict)


_HISTORICAL_CUTOFF_DATE = "r.race_date"
_FEATURE_CTES = indicator_feature_ctes(_HISTORICAL_CUTOFF_DATE)


_SQL = f"""
{_FEATURE_CTES}
SELECT
    :race_code AS race_code,
    historical_cutoff_date AS history_before_date,
    horse_number,
    horse_id,
    horse_name,
    starts AS historical_starts,
    wins AS historical_wins,
    top3_finishes AS historical_top3,
    average_finish_position,
    average_win_odds,
    average_last_three_furlongs,
    aptitude_score,
    recent_form_score,
    distance_aptitude_score,
    course_aptitude_score,
    closing_score,
    time_quality_score,
    speed_quality_score,
    speed_quality_coverage_raw AS speed_quality_coverage,
    speed_baseline_level,
    '{INDICATOR_MODEL_VERSION}' AS indicator_version,
    (starts > 0) AS has_history
FROM component_scores
ORDER BY horse_number
LIMIT :limit
"""


def build_get_horse_feature_matrix_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したget_horse_feature_matrix関数を作る。"""

    async def get_horse_feature_matrix(
        race_code: RaceCode,
    ) -> GetHorseFeatureMatrixOutput:
        """対象レースの前日までの出走馬特徴量行列を取得する。

        近走出走数、勝利数、複勝圏数、平均着順、オッズ、末脚を馬ごとに返す。対象レース日を排他的な基準にし、
        対象日以降の結果は特徴量へ混ぜない。
        欠損はnullのまま保持し、暗黙の平均値補完は行わない。
        採用済みJSONがある場合は、実際の集計レシピによる能力特徴量と前処理契約も返す。
        補完や欠測分岐は推論時にその契約に従って適用し、表示する元の特徴量とは区別する。

        Args:
            race_code: 対象レースのレースコード。

        Returns:
            出走馬ごとの特徴量行。
        """

        race_date_from_code(race_code)
        model = load_active_model()
        if model is not None:
            import math

            from jvdata_bridge_mcp.ml.features import COMPONENTS, nullable_values
            from jvdata_bridge_mcp.ml.live import live_features

            cards, values, boundary, provenance = await live_features(
                database,
                race_code,
                feature_recipe=model.get("feature_recipe", "reference"),
                extended=model["feature_version"] == "prior-date-market-free-v2",
            )

            def nullable(value: object) -> object:
                return None if isinstance(value, float) and not math.isfinite(value) else value

            model_rows = []
            for card, f in zip(cards, values, strict=True):
                summary = provenance["summaries"][card["horse_id"]]
                model_rows.append(
                    {
                        "race_code": race_code,
                        "history_before_date": str(card["race_date"]),
                        "horse_number": card["horse_number"],
                        "horse_id": card["horse_id"],
                        "horse_name": card.get("horse_name"),
                        "historical_starts": summary["starts"],
                        "historical_wins": summary["wins"],
                        "historical_top3": summary["top3"],
                        "average_finish_position": nullable(summary["average_finish_position"]),
                        "average_win_odds": None,
                        "average_last_three_furlongs": nullable(summary["average_last_three_furlongs"]),
                        **{f"{name}_score": nullable(f[f"{name}_score"]) for name in COMPONENTS[:7]},
                        "speed_quality_coverage": nullable(f["speed_coverage"]),
                        "speed_baseline_level": None,
                        "indicator_version": model["feature_version"],
                        "has_history": summary["starts"] > 0,
                    }
                )
            return GetHorseFeatureMatrixOutput(
                features=model_rows,
                ability_features=[
                    {"horse_number": c["horse_number"], **{k: nullable(f[k]) for k in model["features"]}}
                    for c, f in zip(cards, values, strict=True)
                ],
                model_provenance=nullable_values(
                    {
                        "model_version": model["model_version"],
                        "boundary": boundary.model_dump(),
                        "feature_recipe": model.get("feature_recipe", "reference"),
                        "preprocessing": model.get("preprocessing"),
                        **provenance,
                    }
                ),
            )
        await ensure_speed_quality_available(database, race_code)
        rows = await fetch_rows(
            database,
            _SQL,
            {"race_code": race_code, "limit": 100},
            statement_timeout_seconds=PREDICTION_STATEMENT_TIMEOUT_SECONDS,
        )
        return GetHorseFeatureMatrixOutput(features=rows)

    return get_horse_feature_matrix
