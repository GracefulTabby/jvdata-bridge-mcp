"""モデルを計算する前に、対象レースの必要データと適用域を確認する。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.ml.artifacts import model_status
from jvdata_bridge_mcp.tools._race_boundary import resolve_race_boundary
from jvdata_bridge_mcp.tools.prediction._speed_read_model import SpeedQualityStatus, get_speed_quality_status

MODEL_TOOLS = (
    "get_horse_feature_matrix",
    "simulate_race_scenarios",
    "predict_race_probabilities",
    "get_composite_analysis",
)


class ModelToolStatus(BaseModel):
    tool_name: str
    prerequisites_ready: bool
    blockers: list[str] = Field(default_factory=list)
    position_domain_supported: bool | None = None
    limitations: list[str] = Field(default_factory=list)


class PredictionModelStatusOutput(BaseModel):
    race_code: str
    analysis_at: str
    speed_quality: SpeedQualityStatus | None = None
    check_error: str | None = None
    tools: list[ModelToolStatus]
    independent_forecast_requires_speed_quality: bool = False
    check_scope: str = "stored_prerequisites_and_documented_domain; not_accuracy_or_calibration"
    ability_model: dict[str, object] = Field(default_factory=dict)


async def load_prediction_model_status(
    database: JvDataDatabase, race_code: str, *, analysis_at: str
) -> PredictionModelStatusOutput:
    asset = model_status()
    try:
        speed = await get_speed_quality_status(database, race_code, analysis_at=analysis_at)
    except SQLAlchemyError as error:
        # An optional model store must not block acquisition of independent forecast facts.
        return PredictionModelStatusOutput(
            race_code=race_code,
            analysis_at=analysis_at,
            check_error=f"model_prerequisite_check_failed:{type(error).__name__}",
            ability_model=asset,
            tools=[
                ModelToolStatus(
                    tool_name=name,
                    prerequisites_ready=False,
                    blockers=["model_prerequisite_check_failed"],
                )
                for name in MODEL_TOOLS
            ],
        )
    tools = []
    for name in MODEL_TOOLS:
        limitations = [
            "Fixed features use completed prior-date results; live descriptive evidence has a broader boundary."
        ]
        position_domain = None
        if name == "simulate_race_scenarios":
            limitations.append(
                "Corner-position distributions require 8–18 declared entries and a flat turf/dirt course."
            )
            position_domain = (
                8 <= speed.declared_entries <= 18
                and str(speed.track_code or "")[:1] in {"1", "2"}
                and speed.distance_meters is not None
                and not (speed.venue_code == "04" and speed.track_code == "10" and speed.distance_meters == 1000)
            )
            if not position_domain:
                limitations.append(
                    "Current field is outside the corner-position domain; that part of the output is unavailable."
                )
        tools.append(
            ModelToolStatus(
                tool_name=name,
                prerequisites_ready=speed.ready,
                blockers=[] if speed.ready else [speed.status],
                position_domain_supported=position_domain,
                limitations=limitations,
            )
        )
        if name == "simulate_race_scenarios":
            tools[-1].prerequisites_ready = speed.declared_entries > 0
            tools[-1].blockers = [] if speed.declared_entries > 0 else [speed.status]
            tools[-1].limitations.append(
                "Pace and corner coefficients do not use speed; missing speed display features do not block them."
            )
    if asset["status"] == "ready":
        domain = 3 <= speed.declared_entries <= 18 and str(speed.track_code or "")[:1] in {"1", "2"}
        for tool in tools:
            if tool.tool_name in {"get_horse_feature_matrix", "predict_race_probabilities"}:
                tool.prerequisites_ready = domain
                tool.blockers = [] if domain else ["ability_model_domain_or_cards_unavailable"]
                tool.limitations.append(
                    "Approved ability model handles missing speed through its stored preprocessing contract: native missing branches or training-fitted imputation."
                )
    if asset["status"] == "invalid_asset":
        for tool in tools:
            if tool.tool_name in {"get_horse_feature_matrix", "predict_race_probabilities"}:
                tool.prerequisites_ready = False
                tool.blockers = ["invalid_model_asset"]
    return PredictionModelStatusOutput(
        race_code=race_code, analysis_at=analysis_at, speed_quality=speed, tools=tools, ability_model=asset
    )


def build_prediction_model_status_tool(database: JvDataDatabase) -> Callable[..., object]:
    async def prediction_model_status(
        race_code: Annotated[str, Field(description="未発走の対象レースを識別する16桁コード")],
    ) -> PredictionModelStatusOutput:
        """予測モデル4ツールの事前計算被覆・不足理由・適用条件を計算前に確認する。

        不足を別日の行や一様確率で埋めない。速度指標が不足しても、
        race_forecast_contextによる独立した馬評価の材料取得は継続できる。
        準備状態は精度や確率の較正を保証しない。
        """
        boundary = await resolve_race_boundary(database, race_code)
        return await load_prediction_model_status(database, race_code, analysis_at=boundary.analysis_at)

    return prediction_model_status
