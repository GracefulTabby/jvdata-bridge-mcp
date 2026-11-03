"""対象出走馬の過去レースにおける相手関係とクラスの事実。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.race._class_facts import OfficialClassFact, class_join_sql
from jvdata_bridge_mcp.tools.race.condition_changes import _class_display, _class_rank

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]
RecentRuns = Annotated[int, Field(description="馬ごとに調べる過去走数。1〜10、既定5")]


class OpponentRecordBeforeTarget(BaseModel):
    """対象レース以前の完了実績。完全速報も含む。"""

    model_config = ConfigDict(extra="forbid")

    starts: int = Field(description="完了結果の完走数")
    wins: int = Field(description="勝利数")
    top3_finishes: int = Field(description="3着以内の回数")
    provisional_starts: int = 0
    graded_top3_finishes: int = Field(description="G/Jpnグレード表記の競走で3着以内に入った回数")


class PastRaceTopThreeOpponent(BaseModel):
    """過去レースの3着以内に入った相手馬。"""

    model_config = ConfigDict(extra="forbid")

    horse_id: str = Field(description="相手馬の競走馬ID")
    horse_name: str | None = Field(default=None, description="相手馬名")
    is_final_result: bool | None = None
    result_status_code: str | None = None
    finish_position: int = Field(description="その過去レースでの着順")
    time_difference_seconds: float | None = Field(default=None, description="勝馬とのタイム差。秒")
    record_before_target: OpponentRecordBeforeTarget | None = Field(
        default=None, description="今回のレース日より前の確定完走成績。欠損時はnull"
    )


class RaceOpponentClassItem(BaseModel):
    """対象馬の過去走におけるクラスと上位同走馬。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: int = Field(description="今回の馬番")
    horse_name: str | None = Field(default=None, description="今回の馬名")
    run_number: int = Field(description="対象馬内の過去走順位。1が直近")
    past_race_code: str = Field(description="過去レースのコード")
    past_race_date: str = Field(description="過去レースの開催日")
    past_race_name: str | None = Field(default=None, description="過去レース名")
    class_name: str | None = Field(default=None, description="過去レースのグレードまたはクラス名")
    class_rank: int | None = Field(
        default=None,
        description="クラス表記の順序値。相手馬の能力点や勝率ではない。名称を分類できなければnull",
    )
    class_fact: OfficialClassFact | None = None
    fact_id: str | None = None
    current_field_opponents: list[dict[str, object]] = Field(default_factory=list)
    field_size: int | None = Field(default=None, description="過去レースの公式出走頭数")
    is_final_result: bool | None = None
    result_status_code: str | None = None
    horse_finish_position: int | None = Field(default=None, description="対象馬の着順")
    horse_time_difference_seconds: float | None = Field(default=None, description="対象馬の勝馬とのタイム差。秒")
    top_three_opponents: list[PastRaceTopThreeOpponent] = Field(
        description="対象馬を除く、過去レースで3着以内に入った相手馬と今回前の確定実績。全相手の能力評価ではない"
    )


class RaceOpponentClassFactsOutput(BaseModel):
    """対象レース以前の、クラスと同走上位馬の確認可能な事実。"""

    result_policy: str = "complete_official_or_provisional; provisional_can_be_revised"

    race_code: str = Field(description="今回のレースコード")
    recent_runs: int = Field(description="馬ごとに検索した過去走数")
    items: list[RaceOpponentClassItem] = Field(description="出走馬ごとの過去走と同走上位馬")


_SQL = f"""
WITH target AS MATERIALIZED (
    SELECT race_date FROM jv_data.races WHERE race_id = :race_code
), target_cards AS MATERIALIZED (
    SELECT DISTINCT ON (c.race_id, c.horse_number)
        c.horse_number, c.horse_id, c.horse_name
    FROM jv_mart.race_cards AS c
    WHERE c.race_id = :race_code
      AND c.horse_number IS NOT NULL

    ORDER BY c.race_id, c.horse_number, c.horse_id
), ranked_history AS MATERIALIZED (
    SELECT
        c.horse_number, c.horse_id, c.horse_name,
        h.race_id AS past_race_code, h.race_date AS past_race_date,
        r.race_name AS past_race_name,
        history_class.class_name, history_class.class_fact,
        r.starters AS field_size,
        h.finish_position AS horse_finish_position,h.is_final_result,h.result_status_code,
        CASE WHEN h.finish_position = 1 THEN 0.0 ELSE h.time_difference_seconds END
            AS horse_time_difference_seconds,
        ROW_NUMBER() OVER (
            PARTITION BY c.horse_id ORDER BY h.race_date DESC, h.race_id DESC
        ) AS run_number
    FROM target_cards AS c
    CROSS JOIN target AS t
    JOIN jv_mart.horse_runs AS h
      ON h.horse_id = c.horse_id
     AND {before_target_race_sql("h", "t.race_date")}
     AND (h.is_final_result IS TRUE OR h.result_status_code='provisional_complete')
     AND h.is_complete_result IS TRUE
     AND h.is_starter IS TRUE
    JOIN jv_data.races AS r ON r.race_id = h.race_id
    LEFT JOIN LATERAL (SELECT horse_age FROM jv_mart.race_cards
        WHERE race_id = h.race_id AND horse_id = h.horse_id
        ORDER BY horse_number LIMIT 1) pc ON TRUE
    {class_join_sql("r", "history_class", "pc.horse_age")}
), selected_history AS MATERIALIZED (
    SELECT * FROM ranked_history WHERE run_number <= :recent_runs
)
SELECT
    h.horse_number, h.horse_id, h.horse_name, h.run_number,
    h.past_race_code, h.past_race_date, h.past_race_name,
    h.class_name, h.class_fact, h.field_size, h.horse_finish_position,
    h.horse_time_difference_seconds,h.is_final_result,h.result_status_code,
    peer.horse_id AS opponent_horse_id,
    peer.horse_name AS opponent_horse_name,
    peer.finish_position AS opponent_finish_position,peer.is_final_result AS opponent_is_final_result,
    peer.result_status_code AS opponent_result_status_code,
    peer.horse_id IN (SELECT horse_id FROM target_cards) AS in_current_field,
    CASE WHEN peer.finish_position = 1 THEN 0.0 ELSE peer.time_difference_seconds END
        AS opponent_time_difference_seconds
FROM selected_history AS h
LEFT JOIN jv_mart.race_results AS peer
  ON peer.race_id = h.past_race_code
 AND peer.horse_id <> h.horse_id
 AND (peer.finish_position BETWEEN 1 AND 3 OR peer.horse_id IN (SELECT horse_id FROM target_cards))
 AND (peer.is_final_result IS TRUE OR peer.result_status_code='provisional_complete')
 AND peer.is_complete_result IS TRUE
 AND peer.is_starter IS TRUE
 AND peer.is_finisher IS TRUE
ORDER BY h.horse_number, h.run_number, peer.finish_position, peer.horse_number
"""


_RECORD_SQL = f"""
WITH target AS (
    SELECT race_date FROM jv_data.races WHERE race_id = :race_code
)
SELECT
    h.horse_id,
    COUNT(*) AS starts,COUNT(*) FILTER (WHERE h.is_final_result IS FALSE) AS provisional_starts,
    COUNT(*) FILTER (WHERE h.finish_position = 1) AS wins,
    COUNT(*) FILTER (WHERE h.finish_position BETWEEN 1 AND 3) AS top3_finishes,
    COUNT(*) FILTER (
        WHERE h.finish_position BETWEEN 1 AND 3
          AND COALESCE(h.grade_name, '') ~* '^(G(1|2|3|I|II|III)|JPN(1|2|3))'
    ) AS graded_top3_finishes
FROM jv_mart.horse_runs AS h
CROSS JOIN target AS t
WHERE h.horse_id = ANY(:opponent_ids)
  AND {before_target_race_sql("h", "t.race_date")}
  AND (h.is_final_result IS TRUE OR h.result_status_code='provisional_complete')
  AND h.is_complete_result IS TRUE
  AND h.is_starter IS TRUE
  AND h.is_finisher IS TRUE
GROUP BY h.horse_id
"""


def _items_from_rows(
    rows: list[dict[str, object]],
    records: dict[str, OpponentRecordBeforeTarget] | None = None,
) -> list[RaceOpponentClassItem]:
    """同じ過去走の相手馬行を一つの説明可能なFactへ束ねる。"""

    grouped: dict[tuple[int, str], RaceOpponentClassItem] = {}
    opponent_records = records or {}
    for row in rows:
        horse_number = int(row["horse_number"])
        past_race_code = str(row["past_race_code"])
        key = (horse_number, past_race_code)
        if key not in grouped:
            class_name = _class_display(row.get("class_name"))
            grouped[key] = RaceOpponentClassItem(
                horse_number=horse_number,
                horse_name=str(row["horse_name"]) if row.get("horse_name") is not None else None,
                run_number=int(row["run_number"]),
                past_race_code=past_race_code,
                past_race_date=str(row["past_race_date"]),
                past_race_name=str(row["past_race_name"]) if row.get("past_race_name") is not None else None,
                class_name=class_name,
                class_rank=_class_rank(class_name),
                class_fact=row.get("class_fact"),
                fact_id=f"jra-run:{row['past_race_code']}:{row.get('horse_id', 'unknown')}",
                field_size=int(row["field_size"]) if row.get("field_size") is not None else None,
                is_final_result=row.get("is_final_result"),
                result_status_code=row.get("result_status_code"),
                horse_finish_position=(
                    int(row["horse_finish_position"]) if row.get("horse_finish_position") is not None else None
                ),
                horse_time_difference_seconds=(
                    float(row["horse_time_difference_seconds"])
                    if row.get("horse_time_difference_seconds") is not None
                    else None
                ),
                top_three_opponents=[],
            )
        if row.get("opponent_horse_id") is not None and row.get("opponent_finish_position") is not None:
            if row.get("in_current_field"):
                grouped[key].current_field_opponents.append(
                    {
                        "horse_id": str(row["opponent_horse_id"]),
                        "horse_name": row.get("opponent_horse_name"),
                        "finish_position": row.get("opponent_finish_position"),
                        "time_difference_seconds": row.get("opponent_time_difference_seconds"),
                        "evidence_scope": "direct_meeting_in_this_past_race",
                    }
                )
            if not 1 <= int(row["opponent_finish_position"]) <= 3:
                continue
            grouped[key].top_three_opponents.append(
                PastRaceTopThreeOpponent(
                    horse_id=str(row["opponent_horse_id"]),
                    horse_name=(
                        str(row["opponent_horse_name"]) if row.get("opponent_horse_name") is not None else None
                    ),
                    finish_position=int(row["opponent_finish_position"]),
                    time_difference_seconds=(
                        float(row["opponent_time_difference_seconds"])
                        if row.get("opponent_time_difference_seconds") is not None
                        else None
                    ),
                    is_final_result=row.get("opponent_is_final_result"),
                    result_status_code=row.get("opponent_result_status_code"),
                    record_before_target=opponent_records.get(str(row["opponent_horse_id"])),
                )
            )
    return list(grouped.values())


def build_race_opponent_class_facts_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したrace_opponent_class_facts関数を作る。"""

    async def race_opponent_class_facts(
        race_code: RaceCode,
        recent_runs: RecentRuns = 5,
    ) -> RaceOpponentClassFactsOutput:
        """対象馬の過去走のクラスと、同走した3着以内の相手馬を一括取得する。

        対象レースと現在より前の完了結果を使い、完全速報も状態付きで含める。クラス順序値は
        表記を揃えるための便宜値で、相手馬の能力点や勝率ではない。
        相手馬には対象レース前までの完走・勝利・3着以内・重賞3着以内回数を添える。
        同走上位馬だけの抜粋であり、レース全頭の強さを要約するものではない。

        Args:
            race_code: 対象レースを識別するレースコード。
            recent_runs: 馬ごとに確認する過去走数。1〜10、既定5。

        Returns:
            出走馬別の過去レースのクラス、着順、勝馬との差、同走上位馬と確定実績。
        """

        bounded_runs = max(1, min(recent_runs, 10))
        rows = await fetch_rows(
            database,
            _SQL,
            {"race_code": race_code, "recent_runs": bounded_runs},
            statement_timeout_seconds=60,
        )
        opponent_ids = sorted({str(row["opponent_horse_id"]) for row in rows if row.get("opponent_horse_id")})
        record_rows = (
            await fetch_rows(
                database,
                _RECORD_SQL,
                {"race_code": race_code, "opponent_ids": opponent_ids},
                statement_timeout_seconds=60,
            )
            if opponent_ids
            else []
        )
        records = {
            str(row["horse_id"]): OpponentRecordBeforeTarget(
                starts=int(row["starts"]),
                provisional_starts=int(row.get("provisional_starts") or 0),
                wins=int(row["wins"]),
                top3_finishes=int(row["top3_finishes"]),
                graded_top3_finishes=int(row["graded_top3_finishes"]),
            )
            for row in record_rows
        }
        return RaceOpponentClassFactsOutput(
            race_code=race_code,
            recent_runs=bounded_runs,
            items=_items_from_rows(rows, records),
        )

    return race_opponent_class_facts
