"""公式RAの年齢別競走条件を、グレードと別に正規化する共通Fact。"""

from __future__ import annotations

from pydantic import BaseModel, Field

CLASSIFICATION_RULE_VERSION = "jra-ra-age-condition-v1"
CLASS_NAMES = {
    "005": "1勝クラス",
    "010": "2勝クラス",
    "016": "3勝クラス",
    "701": "新馬",
    "702": "未出走",
    "703": "未勝利",
    "999": "オープン",
}
CLASS_RANKS = {"005": 30, "010": 40, "016": 50, "701": 5, "702": 5, "703": 10, "999": 60}


class OfficialClassFact(BaseModel):
    condition_codes_by_age: dict[str, str] = Field(default_factory=dict)
    applicable_condition_code: str | None = None
    applicable_horse_age: int | None = None
    class_name: str | None = None
    class_rank: int | None = None
    grade_code: str | None = None
    grade_name: str | None = None
    classification_status: str = "unknown"
    classification_rule_version: str = CLASSIFICATION_RULE_VERSION
    source_fields: list[str] = Field(default_factory=lambda: ["RA:競走条件コード 年齢別", "RA:グレード"])
    source_race_code: str | None = None
    source_natural_key: str | None = None
    source_artifact_hashes: list[str] = Field(default_factory=list)


def source_race_key_sql(race_id: str) -> str:
    """公式RAの自然キーをrace_idから同じ順序・空白なしの表記にする。"""

    fields = (("f004", 1, 4), ("f005", 5, 4), ("f006", 9, 2), ("f007", 11, 2), ("f008", 13, 2), ("f009", 15, 2))
    args = ", ".join(f"'{key}', substring({race_id} FROM {start} FOR {size})" for key, start, size in fields)
    return f"replace(jsonb_build_object({args})::text, ' ', '')"


def class_join_sql(race_alias: str, output_alias: str, horse_age: str = "NULL::int") -> str:
    """年齢指定を優先し、年齢不明時は全条件が一致する場合だけ補完する。"""

    key = source_race_key_sql(f"{race_alias}.race_id")
    names = " ".join(f"WHEN '{code}' THEN '{name}'" for code, name in CLASS_NAMES.items())
    ranks = " ".join(f"WHEN '{code}' THEN {rank}" for code, rank in CLASS_RANKS.items())
    return f"""
LEFT JOIN LATERAL (
    WITH codes AS (
        SELECT COALESCE(jsonb_object_agg(age_key, value_text), '{{}}'::jsonb) AS code_by_age,
               COUNT(DISTINCT NULLIF(value_text, '000')) AS distinct_conditions,
               MIN(NULLIF(value_text, '000')) AS sole_code,
               array_agg(DISTINCT artifact_hash) AS artifact_hashes
        FROM (
            SELECT DISTINCT ON (s.field_name) s.value_text, s.artifact_hash,
                CASE s.field_name
                    WHEN '競走条件コード 2歳条件' THEN '2'
                    WHEN '競走条件コード 3歳条件' THEN '3'
                    WHEN '競走条件コード 4歳条件' THEN '4'
                    WHEN '競走条件コード 5歳以上条件' THEN '5+'
                    WHEN '競走条件コード 最若年条件' THEN 'youngest'
                END AS age_key
            FROM jv_data.source_field_values s
            WHERE s.record_type = 'RA' AND s.natural_key = {key}
              AND s.field_name IN ('競走条件コード 2歳条件', '競走条件コード 3歳条件',
                '競走条件コード 4歳条件', '競走条件コード 5歳以上条件', '競走条件コード 最若年条件')
            ORDER BY s.field_name, s.artifact_hash
        ) raw
    ), applied AS (
        SELECT codes.*, COALESCE(
            NULLIF(code_by_age ->> CASE WHEN ({horse_age}) >= 5 THEN '5+' ELSE ({horse_age})::text END, '000'),
            CASE WHEN ({horse_age}) IS NULL AND distinct_conditions = 1 THEN sole_code END
        ) AS code
        FROM codes
    )
    SELECT CASE a.code {names} ELSE ref.name END AS class_name,
        CASE a.code {ranks} ELSE NULL END AS class_rank,
        jsonb_build_object('condition_codes_by_age', a.code_by_age,
            'applicable_condition_code', a.code, 'applicable_horse_age', ({horse_age}),
            'class_name', CASE a.code {names} ELSE ref.name END,
            'class_rank', CASE a.code {ranks} ELSE NULL END,
            'grade_code', {race_alias}.grade_code, 'grade_name', {race_alias}.grade_name,
            'classification_status', CASE WHEN a.code IS NULL THEN 'unknown'
                WHEN NULLIF(a.code_by_age ->> CASE WHEN ({horse_age}) >= 5 THEN '5+' ELSE ({horse_age})::text END, '000')
                    IS NOT NULL THEN 'age_condition' ELSE 'consistent_conditions' END,
            'classification_rule_version', '{CLASSIFICATION_RULE_VERSION}',
            'source_race_code', {race_alias}.race_id, 'source_natural_key', {key},
            'source_artifact_hashes', COALESCE(a.artifact_hashes, ARRAY[]::text[]),
            'source_fields', jsonb_build_array('RA:競走条件コード 年齢別', 'RA:グレード')) AS class_fact
    FROM applied a LEFT JOIN jv_ref.code_2007_race_conditions ref ON ref.code = a.code
) {output_alias} ON TRUE
"""
