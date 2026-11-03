"""天候と馬場状態を直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._observation_scope import observation_parameters
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.race._weather import WEATHER_EVENTS_SQL, reconstruct_weather

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class TrackConditionItem(BaseModel):
    """レースの天候・馬場状態の1件。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    race_date: str | None = Field(default=None, description="対象開催日")
    observation_date: str | None = None
    information_policy: str = "current_venue_observation; target_day_unknown"
    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    racecourse_name: str | None = Field(default=None, description="競馬場名")
    weather_code: str | None = Field(default=None, description="天候コード")
    weather_name: str | None = Field(default=None, description="天候")
    turf_condition_code: str | None = Field(default=None, description="芝馬場状態コード")
    turf_condition_name: str | None = Field(default=None, description="芝馬場状態")
    dirt_condition_code: str | None = Field(default=None, description="ダート馬場状態コード")
    dirt_condition_name: str | None = Field(default=None, description="ダート馬場状態")
    track_code: str | None = Field(default=None, description="走路コード")
    track_name: str | None = Field(default=None, description="走路名")
    observation_status: str = "unavailable"
    field_observations: dict[str, object] = Field(default_factory=dict)
    source: str | None = None
    announced_at: str | None = Field(default=None, description="発表日時")
    current_weather_code: str | None = Field(default=None, description="最新天候コード")
    current_weather_name: str | None = Field(default=None, description="最新天候")
    current_turf_condition_code: str | None = Field(default=None, description="最新芝馬場状態コード")
    current_turf_condition_name: str | None = Field(default=None, description="最新芝馬場状態")
    current_dirt_condition_code: str | None = Field(default=None, description="最新ダート馬場状態コード")
    current_dirt_condition_name: str | None = Field(default=None, description="最新ダート馬場状態")


class TrackConditionSummaryOutput(BaseModel):
    """対象レースの天候と芝・ダート馬場状態。"""

    conditions: list[TrackConditionItem] = Field(description="天候と馬場状態")
    analysis_at: str | None = None


_SQL = WEATHER_EVENTS_SQL


def build_track_condition_summary_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したtrack_condition_summary関数を作る。"""

    async def track_condition_summary(
        race_code: RaceCode,
    ) -> TrackConditionSummaryOutput:
        """対象レースの天候と馬場状態を取得する。

        対象レースに対する現在のWF通知を返す。過去時点の再現は行わない。
        天候・芝・ダートの差分通知を項目ごとに復元し、0は未変更として扱う。
        observation_dateを必ず確認し、過去の観測を対象日の馬場として扱わない。
        対象レース当日の結果データから天候・馬場を補完しない。

        Args:
            race_code: 対象レースを識別するレースコード。

        Returns:
            天候、芝・ダート馬場、走路の状態。
        """

        parameters = observation_parameters(race_code)
        rows = await fetch_rows(database, _SQL, parameters)
        if not rows:
            return TrackConditionSummaryOutput(conditions=[], analysis_at=str(parameters["analysis_at"]))
        first = rows[0]
        weather = reconstruct_weather(rows, analysis_at=str(parameters["analysis_at"]))
        payload = {
            key: first.get(key)
            for key in ("race_code", "racecourse_code", "racecourse_name", "track_code", "track_name")
        }
        payload["race_date"] = first.get("target_race_date")
        payload.update(weather)
        payload["information_policy"] = (
            "current_WF_events; target_day_observed"
            if weather.get("observation_date") == parameters["target_race_date"]
            else "current_venue_observation; target_day_unknown"
        )
        for field in ("weather", "turf_condition", "dirt_condition"):
            for suffix in ("code", "name"):
                payload[f"{field}_{suffix}"] = weather.get(f"current_{field}_{suffix}")
        return TrackConditionSummaryOutput(conditions=[payload], analysis_at=str(parameters["analysis_at"]))

    return track_condition_summary
