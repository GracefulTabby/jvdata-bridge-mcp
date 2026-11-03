"""単複・連勝式を同一発表時点で取得する。"""

import math
import re
from typing import Annotated, Callable, Literal

from pydantic import BaseModel, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._observation_scope import observation_parameters
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools._source_timing import SourceTiming, source_timing

BetType = Literal["win", "place", "bracket_quinella", "quinella", "wide", "exacta", "trio", "trifecta"]


class CombinationOdds(BaseModel):
    combination: str
    horse_number_1: int | None = None
    horse_number_2: int | None = None
    horse_number_3: int | None = None
    frame_number_1: int | None = None
    frame_number_2: int | None = None
    odds: float | None = None
    odds_minimum: float | None = None
    odds_maximum: float | None = None
    odds_status: str
    popularity: int | None = None
    horse_name: str | None = None
    probability_comparison: dict[str, object] | None = Field(
        default=None, description="明示入力された凍結確率との研究用比較。投資価値の確認ではない"
    )


class OddsTableOutput(BaseModel):
    race_code: str
    bet_type: str
    observation_scope: str
    analysis_at: str
    snapshot_at: str | None = None
    data_stage_code: str | None = None
    source: str = "jv_mart.odds_history_occurrences"
    source_artifact_hash: str | None = None
    pre_race_verified: bool = Field(
        default=False, description="発走前の発表観測であること。情報の新しさはsource_timingで確認"
    )
    source_timing: SourceTiming
    missing_reason: str | None = None
    snapshot_row_count: int = 0
    matching_row_count: int = 0
    returned_count: int = 0
    truncated: bool = False
    missing_combinations: list[str] = Field(default_factory=list)
    items: list[CombinationOdds] = Field(default_factory=list)
    scheduled_start_at: str | None = None
    expected_entry_count: int | None = None
    field_coverage_complete: bool | None = None
    missing_horse_numbers: list[int] = Field(default_factory=list)
    unexpected_horse_numbers: list[int] = Field(default_factory=list)


_SQL = """
WITH target AS MATERIALIZED (
    SELECT race_date,scheduled_start_at FROM jv_data.races WHERE race_id=:race_code
), cards AS MATERIALIZED (
    SELECT DISTINCT ON (horse_number) horse_number,horse_name
    FROM jv_mart.race_cards WHERE race_id=:race_code AND horse_number IS NOT NULL
    ORDER BY horse_number
), snapshot AS MATERIALIZED (
    SELECT published_at,artifact_hash,source_sequence,data_stage_code
    FROM jv_mart.odds_history_occurrences
    WHERE race_id=:race_code AND bet_type=:bet_type AND data_stage_code IN ('1','2')
      AND published_at<=CAST(:analysis_local AS timestamp)
      AND ((SELECT race_date FROM target)>CAST(:today AS date)
        OR CAST(:analysis_local AS timestamp)<(SELECT scheduled_start_at FROM target))
      AND ((SELECT scheduled_start_at FROM target) IS NULL
        OR published_at<(SELECT scheduled_start_at FROM target))
    ORDER BY published_at DESC,source_sequence DESC,artifact_hash DESC LIMIT 1
), observations AS MATERIALIZED (
    SELECT DISTINCT ON (o.combination) o.*
    FROM jv_mart.odds_history_occurrences o JOIN snapshot s
      ON o.published_at=s.published_at AND o.artifact_hash=s.artifact_hash AND o.source_sequence=s.source_sequence
    WHERE o.race_id=:race_code AND o.bet_type=:bet_type AND o.data_stage_code IN ('1','2')
    ORDER BY o.combination
), matching AS MATERIALIZED (
    SELECT * FROM observations WHERE (:all_combinations OR combination=ANY(CAST(:combinations AS text[])))
)
SELECT s.published_at AS snapshot_at,s.artifact_hash AS source_artifact_hash,
       (SELECT scheduled_start_at FROM target) AS scheduled_start_at,
       ARRAY(SELECT horse_number FROM cards ORDER BY horse_number) AS expected_horse_numbers,
       ARRAY(SELECT horse_number_1 FROM observations ORDER BY horse_number_1) AS observed_horse_numbers,
       (SELECT COUNT(*) FROM observations) AS snapshot_row_count,
       (SELECT COUNT(*) FROM matching) AS matching_row_count,
       m.combination,m.horse_number_1,m.horse_number_2,m.horse_number_3,
       m.frame_number_1,m.frame_number_2,m.odds,m.odds_minimum,m.odds_maximum,
       m.odds_status,m.popularity,s.data_stage_code,c.horse_name
FROM snapshot s LEFT JOIN matching m ON TRUE
LEFT JOIN cards c ON :bet_type IN ('win','place') AND c.horse_number=m.horse_number_1
ORDER BY m.combination LIMIT :limit
"""


def normalize_combinations(bet_type: BetType, combinations: list[str] | None) -> list[str]:
    if combinations == []:
        raise ValueError("combinations must not be empty; omit it to request all combinations")
    if bet_type not in {"win", "place", "bracket_quinella", "quinella", "wide", "exacta", "trio", "trifecta"}:
        raise ValueError("unsupported bet_type")
    width = 1 if bet_type == "bracket_quinella" else 2
    size = 1 if bet_type in {"win", "place"} else 3 if bet_type in {"trio", "trifecta"} else 2
    normalized = []
    for value in combinations or []:
        if re.fullmatch(r"[0-9]+", value) and len(value) == width * size:
            numbers = [int(value[i : i + width]) for i in range(0, len(value), width)]
        elif re.fullmatch(r"[0-9]{1,2}(?:-[0-9]{1,2})*", value):
            numbers = [int(n) for n in value.split("-")]
        else:
            raise ValueError("combinations must use ASCII numbers, e.g. 03-06-07 or 030607")
        if len(numbers) != size or any(n < 1 or n > (8 if width == 1 else 18) for n in numbers):
            raise ValueError("combination size or horse/frame number is invalid")
        if width == 2 and len(set(numbers)) != len(numbers):
            raise ValueError("a horse cannot occur twice in a combination")
        if bet_type in {"bracket_quinella", "quinella", "wide", "trio"}:
            numbers.sort()
        normalized.append("".join(f"{n:0{width}d}" for n in numbers))
    return sorted(set(normalized))


def build_odds_table_tool(database: JvDataDatabase) -> Callable[..., object]:
    async def odds_table(
        race_code: Annotated[str, Field(description="16桁のレースコード")],
        bet_type: Annotated[BetType, Field(description="trio=3連複、trifecta=3連単、quinella=馬連、wide=ワイド等")],
        combinations: Annotated[list[str] | None, Field(description="省略は全組合せ。例: ['3-6-7','2-6-7']")] = None,
        limit: Annotated[int, Field(description="返却上限。1〜10000、切り詰めを明示")] = 1000,
        model_probabilities: Annotated[
            dict[str, float] | None,
            Field(
                max_length=200,
                description="凍結した組合せの的中確率を必要時だけ指定。ワイドは表示用正規化値ではなく的中確率。モデルは再実行しない",
            ),
        ] = None,
    ) -> OddsTableOutput:
        """3連複など全8券種の発走前オッズを同一発表観測から取得する。

        対象レースと現在時刻から発走前の発表観測を自動選択する。最終・確定段階は代用しない。
        無投票・取消行も状態付きで保持し、指定買い目の欠測と返却上限を区別する。
        馬の独立評価を固定してから照会する。
        source_timingは発表からの経過秒数を返す。取得時刻と取り込み遅延は出力しない。5分は表示用の目安で、
        古い観測も除外しない。pre_race_verifiedは配信元の最新性を保証しない。
        """
        requested = normalize_combinations(bet_type, combinations)
        frozen = {}
        for combination, probability in (model_probabilities or {}).items():
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("model probabilities must be finite and between zero and one")
            code = normalize_combinations(bet_type, [combination])[0]
            if code in frozen and frozen[code] != probability:
                raise ValueError("conflicting probabilities for the same combination")
            frozen[code] = probability
        parameters = observation_parameters(race_code)
        cap = max(1, min(limit, 10000))
        rows = await fetch_rows(
            database,
            _SQL,
            {
                **parameters,
                "bet_type": bet_type,
                "all_combinations": not requested,
                "combinations": requested,
                "limit": cap,
            },
        )
        first = rows[0] if rows else {}
        fields = set(CombinationOdds.model_fields)
        items = [
            CombinationOdds(**{k: v for k, v in row.items() if k in fields}) for row in rows if row.get("combination")
        ]
        if frozen:
            from jvdata_bridge_mcp.ml.market import compare_observation

            for item in items:
                if item.combination in frozen and item.odds_status == "available" and first.get("snapshot_at"):
                    item.probability_comparison = compare_observation(
                        frozen[item.combination],
                        item.model_dump(),
                        calibration_status="caller_supplied_frozen_probability; calibration_not_verified_by_odds_tool",
                    )
        matching = int(first.get("matching_row_count") or 0)
        # Requested combinations may extend beyond the returned page; do not call those absent.
        found = {item.combination for item in items}
        missing = sorted(set(requested) - found) if matching <= cap else []
        expected = {int(n) for n in first.get("expected_horse_numbers", []) if n is not None}
        observed = {int(n) for n in first.get("observed_horse_numbers", []) if n is not None}
        single = bet_type in {"win", "place"}
        missing_horses = sorted(expected - observed) if single else []
        unexpected_horses = sorted(observed - expected) if single and expected else []
        return OddsTableOutput(
            race_code=race_code,
            bet_type=bet_type,
            observation_scope=str(parameters["observation_scope"]),
            analysis_at=str(parameters["analysis_at"]),
            scheduled_start_at=first.get("scheduled_start_at"),
            expected_entry_count=len(expected) or None,
            field_coverage_complete=not missing_horses and not unexpected_horses if single and expected else None,
            missing_horse_numbers=missing_horses,
            unexpected_horse_numbers=unexpected_horses,
            snapshot_at=first.get("snapshot_at"),
            data_stage_code=first.get("data_stage_code"),
            source_artifact_hash=first.get("source_artifact_hash"),
            pre_race_verified=bool(first.get("snapshot_at")),
            source_timing=source_timing(
                str(parameters["analysis_at"]),
                published_at=first.get("snapshot_at"),
                observed=bool(first.get("snapshot_at")),
                advisory_max_age_seconds=300,
            ),
            missing_reason="no_eligible_prefinal_observation"
            if not first
            else "requested_combinations_missing"
            if missing
            else "incomplete_field_coverage"
            if missing_horses or unexpected_horses
            else None,
            snapshot_row_count=int(first.get("snapshot_row_count") or 0),
            matching_row_count=matching,
            returned_count=len(items),
            truncated=matching > len(items),
            missing_combinations=missing,
            items=items,
        )

    return odds_table
