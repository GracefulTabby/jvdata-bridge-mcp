"""レース概要を直接SQLで取得するMCPツール。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Annotated, Callable

from pydantic import BaseModel, Field
from sqlalchemy import text

from jvdata_bridge_mcp.database import JvDataDatabase

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class RaceGrade(BaseModel):
    """レース格。"""

    code: str = Field(description="グレードコード")
    name: str = Field(description="グレード名")


class RaceSummaryOutput(BaseModel):
    """レース概要のツール固有出力。"""

    race_code: str = Field(description="レースコード")
    scheduled_start_at: str | None = None
    timezone: str = "Asia/Tokyo"
    race_name: str = Field(description="レース名")
    race_date: str | None = Field(default=None, description="開催日")
    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    racecourse_name: str | None = Field(default=None, description="競馬場名")
    distance: int | None = Field(default=None, description="距離。メートル単位")
    track_type: str | None = Field(default=None, description="走路種別")
    track_code: str | None = Field(default=None, description="走路コード")
    declared_entry_count: int | None = Field(default=None, description="登録頭数")
    starter_count: int | None = Field(default=None, description="出走頭数")
    grade: RaceGrade | None = Field(default=None, description="レース格")


_SQL = text(
    """
    SELECT
        race_id AS race_code,
        race_name,
        race_date,
        scheduled_start_at,
        venue_code AS racecourse_code,
        venue_name AS racecourse_name,
        distance_meters AS distance,
        track_name AS track_type,
        track_code,
        registered_horses AS declared_entry_count,
        NULL::int AS starter_count,
        grade_code,
        grade_name
    FROM jv_data.races
    WHERE race_id = :race_code
    """
)


def _text_value(value: object | None) -> str | None:
    """DB値を公開文字列へ変換する。"""

    if value is None:
        return None
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return str(value)


def _output_from_row(row: Mapping[str, object]) -> RaceSummaryOutput:
    """SELECT結果をPydantic出力へ変換する。"""

    grade_code = _text_value(row.get("grade_code"))
    grade_name = _text_value(row.get("grade_name"))
    grade = None
    if grade_code or grade_name:
        grade = RaceGrade(code=grade_code or "", name=grade_name or grade_code or "")
    distance = row.get("distance")
    declared = row.get("declared_entry_count")
    starters = row.get("starter_count")
    return RaceSummaryOutput(
        race_code=_text_value(row.get("race_code")) or "",
        race_name=_text_value(row.get("race_name")) or "",
        race_date=_text_value(row.get("race_date")),
        scheduled_start_at=_text_value(row.get("scheduled_start_at")),
        racecourse_code=_text_value(row.get("racecourse_code")),
        racecourse_name=_text_value(row.get("racecourse_name")),
        distance=None if distance is None else int(distance),
        track_type=_text_value(row.get("track_type")),
        track_code=_text_value(row.get("track_code")),
        declared_entry_count=None if declared is None else int(declared),
        starter_count=None if starters is None else int(starters),
        grade=grade,
    )


def build_race_summary_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを閉包で注入したrace_summary関数を作る。"""

    async def race_summary(race_code: RaceCode) -> RaceSummaryOutput:
        """対象レースの概要を取得する。

        開催日、競馬場、レース名、走路種別、距離、登録頭数、グレードを返す。
        入力値はSQLのバインドパラメータとして扱い、公開関数から直接read modelを読む。

        Args:
            race_code: 対象レースを識別する16桁のレースコード。

        Returns:
            対象レースの基本情報。
        """

        async with database.read_connection() as connection:
            row = (await connection.execute(_SQL, {"race_code": race_code})).mappings().one_or_none()
        if row is None:
            raise ValueError(f"race was not found: {race_code}")
        return _output_from_row(row)

    return race_summary
