"""馬体重の現行read modelと、版を一意に確認できるWH原票を読む。"""

from __future__ import annotations

import json


def wh_natural_key(race_code: str) -> str:
    """16桁レースコードをWH原票の自然キーに変換する。"""

    return json.dumps(
        {
            "f004": race_code[:4],
            "f005": race_code[4:8],
            "f006": race_code[8:10],
            "f007": race_code[10:12],
            "f008": race_code[12:14],
            "f009": race_code[14:16],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


WEIGHT_CTES = """
wh_artifact AS MATERIALIZED (
    SELECT MIN(g.artifact_hash) AS artifact_hash
    FROM jv_data.source_group_values AS g
    WHERE g.record_type = 'WH'
      AND g.natural_key = :wh_natural_key
      AND g.child_field_name = '馬番'
    HAVING COUNT(DISTINCT g.artifact_hash) = 1
), wh_fields AS MATERIALIZED (
    SELECT
        g.item_number,
        MAX(g.value_text) FILTER (WHERE g.child_field_name = '馬番') AS horse_number_text,
        MAX(g.value_text) FILTER (WHERE g.child_field_name = '馬体重') AS body_weight_text,
        MAX(g.value_text) FILTER (WHERE g.child_field_name = '増減符号') AS change_sign,
        MAX(g.value_text) FILTER (WHERE g.child_field_name = '増減差') AS change_text
    FROM jv_data.source_group_values AS g
    JOIN wh_artifact AS a ON a.artifact_hash = g.artifact_hash
    WHERE g.record_type = 'WH'
      AND g.natural_key = :wh_natural_key
      AND g.child_field_name IN ('馬番', '馬体重', '増減符号', '増減差')
    GROUP BY g.item_number
), wh_weights AS MATERIALIZED (
    SELECT
        horse_number_text::smallint AS horse_number,
        CASE
            WHEN body_weight_text ~ '^[0-9]{3}$' AND body_weight_text BETWEEN '002' AND '998'
            THEN body_weight_text::smallint
            ELSE NULL
        END AS body_weight_kg,
        CASE
            WHEN body_weight_text IS NULL OR body_weight_text !~ '^[0-9]{3}$'
              OR body_weight_text NOT BETWEEN '002' AND '998'
            THEN NULL
            WHEN change_text ~ '^[0-9]{3}$' AND change_text BETWEEN '000' AND '998'
              AND change_sign = '-' THEN -change_text::smallint
            WHEN change_text ~ '^[0-9]{3}$' AND change_text BETWEEN '000' AND '998'
              AND change_sign = '+' THEN change_text::smallint
            WHEN change_text = '000' THEN 0
            ELSE NULL
        END AS body_weight_change_kg
    FROM wh_fields
    WHERE horse_number_text ~ '^(0[1-9]|1[0-8])$'
), latest_weights AS MATERIALIZED (
    SELECT DISTINCT ON (w.race_id, w.horse_number)
        w.*
    FROM jv_mart.latest_horse_weights AS w
    WHERE w.race_id = :race_code
    ORDER BY w.race_id, w.horse_number, w.announced_at DESC NULLS LAST
)
"""
