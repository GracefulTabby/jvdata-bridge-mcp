"""確定レース結果を直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._result_policy import ResultScope, include_provisional
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.race._ledger import runner_status

RaceCode = Annotated[str, Field(description="取得対象レースのレースコード。省略時は他の条件で検索")]
RaceName = Annotated[str, Field(description="レース名による部分一致検索条件")]
Year = Annotated[int, Field(description="開催年。0の場合は年で絞り込まない")]


class RaceResultItem(BaseModel):
    """確定レース結果の1件。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str = Field(description="レースコード")
    race_date: str | None = Field(default=None, description="開催日")
    racecourse_code: str | None = Field(default=None, description="競馬場コード")
    racecourse_name: str | None = Field(default=None, description="競馬場名")
    race_number: int | None = Field(default=None, description="レース番号")
    race_name: str | None = Field(default=None, description="レース名")
    horse_number: int | None = Field(default=None, description="馬番")
    horse_id: str | None = Field(default=None, description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    abnormality_code: str | None = None
    abnormality_name: str | None = None
    runner_status: str = "not_confirmed"
    finish_position: int | None = Field(default=None, description="着順")
    elapsed_seconds: float | None = Field(default=None, description="走破タイム。秒")
    final_win_odds: float | None = Field(default=None, description="最終単勝オッズ")
    final_win_popularity: int | None = Field(default=None, description="最終単勝人気")
    is_final_result: bool | None = Field(default=None, description="確定結果か")
    is_complete_result: bool | None = None
    result_stage_code: str | None = None
    result_status_code: str | None = None
    is_starter: bool | None = Field(default=None, description="出走馬か")
    is_finisher: bool | None = Field(default=None, description="完走馬か")


class GetRaceResultsOutput(BaseModel):
    """確定着順とオッズを含むレース結果一覧。"""

    result_scope: str = "confirmed_finishers"
    returned_rows: int = 0
    effective_limit: int = 1000
    truncated: bool = False
    status_counts_scope: str = "returned_rows"
    status_counts: dict[str, int] = Field(default_factory=dict)
    result_policy: str = "complete"
    provisional_rows: int = 0
    results: list[RaceResultItem] = Field(description="レース結果一覧")


_SQL = """
SELECT
    rr.race_id AS race_code,
    rr.race_date,
    rr.venue_code AS racecourse_code,
    rr.venue_name AS racecourse_name,
    rr.race_number,
    rr.race_name,
    rr.horse_number,
    rr.horse_id,
    rr.horse_name,
    rr.finish_position,
    rr.elapsed_seconds,
    rr.final_win_odds,
    rr.final_win_popularity,
    rr.is_final_result,
    rr.is_starter,
    rr.is_finisher, rr.abnormality_code, rr.abnormality_name,
    rr.is_complete_result,rr.result_stage_code,rr.result_status_code
FROM jv_mart.race_results AS rr
WHERE (:race_code = '' OR rr.race_id = :race_code)
  AND (:race_name = '' OR rr.race_name ILIKE '%' || :race_name || '%')
  AND (:year = 0 OR EXTRACT(YEAR FROM rr.race_date) = :year)
  AND (rr.is_final_result IS TRUE OR (:include_provisional AND rr.is_complete_result IS TRUE
       AND rr.result_status_code='provisional_complete'))
  AND rr.is_starter IS TRUE
  AND rr.is_finisher IS TRUE
ORDER BY rr.race_date DESC, rr.race_id, rr.finish_position NULLS LAST, rr.horse_number
LIMIT :limit
"""


_ALL_SQL = """
WITH cards AS MATERIALIZED (
 SELECT DISTINCT ON (c.race_id,c.horse_number) c.* FROM jv_mart.race_cards c
 WHERE (:race_code = '' OR c.race_id = :race_code)
   AND (:race_name = '' OR c.race_name ILIKE '%' || :race_name || '%')
   AND (:year = 0 OR EXTRACT(YEAR FROM c.race_date) = :year)
 ORDER BY c.race_id,c.horse_number
)
SELECT c.race_id AS race_code, c.race_date, c.venue_code AS racecourse_code, c.venue_name AS racecourse_name,
 c.race_number,c.race_name,c.horse_number,c.horse_id,c.horse_name,
 rr.finish_position,rr.elapsed_seconds,rr.final_win_odds,rr.final_win_popularity,
 rr.is_final_result,rr.is_starter,rr.is_finisher,rr.is_complete_result,rr.result_stage_code,rr.result_status_code,
 COALESCE(rr.abnormality_code,c.abnormality_code) AS abnormality_code,
 COALESCE(rr.abnormality_name,c.abnormality_name) AS abnormality_name
FROM cards c LEFT JOIN LATERAL (
 SELECT * FROM jv_mart.race_results r WHERE r.race_id = c.race_id AND r.horse_id = c.horse_id
 ORDER BY r.source_record_id LIMIT 1
) rr ON TRUE
ORDER BY c.race_date DESC,c.race_id,c.horse_number LIMIT :limit
"""


def build_get_race_results_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したget_race_results関数を作る。"""

    async def get_race_results(
        race_code: RaceCode = "",
        race_name: RaceName = "",
        year: Year = 0,
        include_all_entries: Annotated[bool, Field(description="取消・除外・中止・未確定も含む全頭台帳")] = False,
        result_scope: ResultScope = "complete",
    ) -> GetRaceResultsOutput:
        """完全なレース結果を取得する。全頭揃った速報は状態付きで返す。

        result_scope=officialは成績段階だけ。include_all_entries=trueは部分速報・未確定・取消も含む全頭台帳。

        レースコードを指定した場合はそのレースを優先し、指定しない場合は
        レース名と開催年で検索する。着順、馬番、馬名、騎手、確定単勝情報を返す。

        Args:
            race_code: 取得対象レースのレースコード。
            race_name: レース名の部分一致条件。
            year: 開催年。0の場合は指定しない。

        Returns:
            確定結果の行一覧。
        """

        rows = await fetch_rows(
            database,
            _ALL_SQL if include_all_entries else _SQL,
            {
                "race_code": race_code,
                "race_name": race_name,
                "year": year,
                "limit": 1_001,
                "include_provisional": include_provisional(result_scope),
            },
        )
        truncated = len(rows) > 1000
        rows = rows[:1000]
        counts: dict[str, int] = {}
        for row in rows:
            status = runner_status(row)
            row["runner_status"] = status
            counts[status] = counts.get(status, 0) + 1
        return GetRaceResultsOutput(
            results=rows,
            result_policy="all_stages_ledger" if include_all_entries else result_scope,
            provisional_rows=sum(
                row.get("is_final_result") is False and row.get("is_complete_result") is True for row in rows
            ),
            status_counts=counts,
            returned_rows=len(rows),
            truncated=truncated,
            result_scope="all_declared_entries"
            if include_all_entries
            else "complete_finishers"
            if result_scope == "complete"
            else "confirmed_finishers",
        )

    return get_race_results
