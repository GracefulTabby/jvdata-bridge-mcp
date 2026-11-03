"""血統と距離適性の傾向を直接SQLで集計するMCPツール。"""

from __future__ import annotations

import json
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]


class BloodlineDistanceBiasItem(BaseModel):
    """血統祖先と距離別成績の1件。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: int | None = Field(default=None, description="馬番")
    horse_id: str = Field(description="競走馬ID")
    horse_name: str | None = Field(default=None, description="馬名")
    ancestor_name: str | None = Field(default=None, description="祖先名")
    ancestor_breeding_id: str | None = None
    family_scope: str = "unknown"
    target_excluded: bool = True
    sample_horses: int = 0
    track_type: str | None = None
    distance_min: int | None = None
    distance_max: int | None = None
    horse_age: int | None = None
    ground: str | None = None
    first_stored_run_only: bool = False
    evidence_start_date: str | None = None
    evidence_end_date: str | None = None
    raw_win_rate: float | None = None
    shrunk_win_rate: float | None = None
    reference_win_rate: float | None = None
    reference_scope: str = "same_surface_distance_range; all_ages_all_ground; stored_JRA"
    shrinkage_prior_starts: int = 20
    uncertainty: str = "descriptive_family_statistics; not_individual_ability; stored_jra_only"
    distance_meters: int | None = Field(default=None, description="距離。m")
    starts: int = Field(description="出走数")
    wins: int = Field(description="勝利数")
    top3_finishes: int = Field(description="3着以内数")
    average_finish_position: float | None = Field(default=None, description="平均着順")


class BloodlineDistanceBiasOutput(BaseModel):
    """対象出走馬の血統祖先と距離別成績。"""

    result_policy: str = "complete_official_or_provisional; provisional_can_be_revised"

    biases: list[BloodlineDistanceBiasItem] = Field(description="血統祖先の距離別成績")


_TARGET_SQL = """
SELECT DISTINCT ON (c.horse_number) c.horse_number,c.horse_id,c.horse_name,c.race_date,c.distance_meters,
    CASE WHEN c.track_name LIKE '%障害%' THEN 'jump' WHEN c.track_name LIKE '%芝%' THEN 'turf' ELSE 'dirt' END AS surface
FROM jv_mart.race_cards c WHERE c.race_id = :race_code

ORDER BY c.horse_number
"""
_PARENT_SQL = """
SELECT horse_id,pedigree_three_generations FROM jv_data.horses WHERE horse_id = ANY(:horse_ids)
"""
_RELATIVE_SQL = """
SELECT horse_id, pedigree_three_generations -> 0 ->> 'f018_a' AS sire_id,
    pedigree_three_generations -> 1 ->> 'f018_a' AS dam_id
FROM jv_data.horses
WHERE pedigree_three_generations -> 0 ->> 'f018_a' = ANY(:ancestor_ids)
   OR pedigree_three_generations -> 1 ->> 'f018_a' = ANY(:ancestor_ids)
"""
_SQL = f"""
WITH links AS MATERIALIZED (
 SELECT * FROM jsonb_to_recordset(CAST(:links AS jsonb)) AS x(family_key text, horse_id text)
), scope_runs AS MATERIALIZED (
 SELECT h.*, CASE WHEN :ground = '' THEN NULL ELSE (SELECT CASE WHEN h.track_name LIKE '%芝%'
     THEN r.turf_condition_name ELSE r.dirt_condition_name END FROM jv_data.races r
     WHERE r.race_id = h.race_id LIMIT 1) END AS ground,
 CASE WHEN CAST(:horse_age AS int) IS NULL THEN NULL ELSE (SELECT c.horse_age FROM jv_mart.race_cards c
     WHERE c.race_id = h.race_id AND c.horse_id = h.horse_id LIMIT 1) END AS horse_age
 FROM jv_mart.horse_runs h WHERE h.horse_id = ANY(:relative_horse_ids)
   AND {before_target_race_sql("h", "CAST(:target_date AS date)")}
   AND (h.is_final_result IS TRUE OR h.result_status_code='provisional_complete') AND h.is_complete_result IS TRUE AND h.is_starter IS TRUE
   AND h.venue_code BETWEEN '01' AND '10'
   AND h.distance_meters BETWEEN :distance_min AND :distance_max
   AND CASE WHEN h.track_name LIKE '%障害%' THEN 'jump' WHEN h.track_name LIKE '%芝%' THEN 'turf' ELSE 'dirt' END = :surface
), eligible AS MATERIALIZED (
 SELECT h.* FROM scope_runs h
 WHERE (:ground = '' OR h.ground = :ground)
   AND (CAST(:horse_age AS int) IS NULL OR h.horse_age = :horse_age)
   AND (:first_stored_run_only = FALSE OR NOT EXISTS (SELECT 1 FROM jv_mart.horse_runs older
       WHERE older.horse_id = h.horse_id AND older.race_date < h.race_date
         AND older.venue_code BETWEEN '01' AND '10' AND (older.is_final_result IS TRUE OR older.result_status_code='provisional_complete') AND older.is_complete_result IS TRUE AND older.is_starter IS TRUE))
)
SELECT l.family_key, COUNT(*) AS starts,COUNT(DISTINCT h.horse_id) AS sample_horses,
    COUNT(*) FILTER (WHERE h.finish_position = 1) AS wins,
    COUNT(*) FILTER (WHERE h.finish_position BETWEEN 1 AND 3) AS top3_finishes,
    AVG(h.finish_position) AS average_finish_position,
    MIN(h.race_date) AS evidence_start_date,MAX(h.race_date) AS evidence_end_date
FROM links l JOIN eligible h ON h.horse_id = l.horse_id GROUP BY l.family_key
"""
_BASELINE_SQL = f"""
SELECT COUNT(*)::float / NULLIF(SUM(starters),0) AS rate FROM jv_data.races baseline_race
WHERE {before_target_race_sql("baseline_race", "CAST(:target_date AS date)")} AND is_complete_result IS TRUE AND starters > 0
  AND venue_code BETWEEN '01' AND '10'
  AND distance_meters BETWEEN :distance_min AND :distance_max
  AND CASE WHEN track_name LIKE '%障害%' THEN 'jump' WHEN track_name LIKE '%芝%' THEN 'turf' ELSE 'dirt' END = :surface
"""


def build_bloodline_distance_bias_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したbloodline_distance_bias関数を作る。"""

    async def bloodline_distance_bias(
        race_code: RaceCode,
        distance_tolerance: int = 200,
        horse_age: int | None = None,
        ground: str = "",
        first_stored_run_only: bool = False,
    ) -> BloodlineDistanceBiasOutput:
        """対象レースの血統と距離適性の傾向を分析する。

        父産駒または同母馬の実出走を集計し、対象馬自身を除く。
        同走路・指定距離範囲、年齢・馬場・保存履歴の初走に絞れる。
        first_stored_run_onlyは生涯初出走や新馬戦の保証ではない。
        縮小率は同走路距離の基準勝率へ20走分縮小した記述値で、個体の能力や勝率ではない。

        Args:
            race_code: 対象レースのレースコード。

        Returns:
            血統祖先・距離別の成績。
        """

        if not 0 <= distance_tolerance <= 2000:
            raise ValueError("distance_tolerance must be between 0 and 2000")
        cards = await fetch_rows(database, _TARGET_SQL, {"race_code": race_code}, disable_jit=True)
        if not cards:
            return BloodlineDistanceBiasOutput(biases=[])
        parents = await fetch_rows(
            database, _PARENT_SQL, {"horse_ids": [c["horse_id"] for c in cards]}, disable_jit=True
        )
        pedigrees = {str(p["horse_id"]): p["pedigree_three_generations"] for p in parents}
        families: dict[str, dict[str, object]] = {}
        for c in cards:
            pedigree = pedigrees.get(str(c["horse_id"])) or []
            for index, relation in enumerate(("sire", "dam")):
                if len(pedigree) <= index:
                    continue
                ancestor = pedigree[index]
                ancestor_id = str(ancestor.get("f018_a") or "").strip()
                if not ancestor_id or ancestor_id == "0000000000":
                    continue
                family_key = f"{c['horse_id']}:{relation}:{ancestor_id}"
                families[family_key] = dict(
                    horse_number=c["horse_number"],
                    horse_id=c["horse_id"],
                    horse_name=c["horse_name"],
                    ancestor_name=str(ancestor.get("f018_b") or "").strip(),
                    ancestor_breeding_id=ancestor_id,
                    family_scope="sire_offspring" if relation == "sire" else "maternal_siblings",
                    distance_meters=c["distance_meters"],
                    distance_min=int(c["distance_meters"]) - distance_tolerance,
                    distance_max=int(c["distance_meters"]) + distance_tolerance,
                    track_type=c["surface"],
                    horse_age=horse_age,
                    ground=ground or None,
                    first_stored_run_only=first_stored_run_only,
                    starts=0,
                    wins=0,
                    top3_finishes=0,
                    sample_horses=0,
                )
        if not families:
            return BloodlineDistanceBiasOutput(biases=[])
        relatives = await fetch_rows(
            database,
            _RELATIVE_SQL,
            {"ancestor_ids": list({r["ancestor_breeding_id"] for r in families.values()})},
            disable_jit=True,
        )
        links = []
        for key, family in families.items():
            parent_key = "sire_id" if family["family_scope"] == "sire_offspring" else "dam_id"
            for relative in relatives:
                if (
                    relative.get(parent_key) == family["ancestor_breeding_id"]
                    and relative["horse_id"] != family["horse_id"]
                ):
                    links.append({"family_key": key, "horse_id": relative["horse_id"]})
        first = next(iter(families.values()))
        parameters = {
            "race_code": race_code,
            "target_date": cards[0]["race_date"],
            "distance_min": first["distance_min"],
            "distance_max": first["distance_max"],
            "surface": first["track_type"],
            "horse_age": horse_age,
            "ground": ground,
            "first_stored_run_only": first_stored_run_only,
            "links": json.dumps(links),
            "relative_horse_ids": list({link["horse_id"] for link in links}),
        }
        statistics = await fetch_rows(database, _SQL, parameters, disable_jit=True) if links else []
        for row in statistics:
            key = str(row.pop("family_key"))
            families[key].update(row)
        baseline = await fetch_rows(database, _BASELINE_SQL, parameters, disable_jit=True)
        reference = baseline[0].get("rate") if baseline else None
        for family in families.values():
            n, wins = int(family["starts"]), int(family["wins"])
            family["reference_win_rate"] = reference
            family["raw_win_rate"] = wins / n if n else None
            family["shrunk_win_rate"] = (
                (wins + 20 * float(reference)) / (n + 20) if n and reference is not None else None
            )
        return BloodlineDistanceBiasOutput(biases=list(families.values()))

    return bloodline_distance_bias
