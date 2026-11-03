"""学習とライブ推論で共有する公式条件・調教母集団のSQL。"""

from jvdata_bridge_mcp.tools.race._class_facts import source_race_key_sql

CLASS_SQL = f"""SELECT r.race_id,
CASE s.field_name WHEN '競走条件コード 2歳条件' THEN '2' WHEN '競走条件コード 3歳条件' THEN '3'
 WHEN '競走条件コード 4歳条件' THEN '4' WHEN '競走条件コード 5歳以上条件' THEN '5+' ELSE 'youngest' END AS age_key,
CASE WHEN COUNT(DISTINCT NULLIF(BTRIM(s.value_text),'000'))=1
 THEN MIN(NULLIF(BTRIM(s.value_text),'000')) END AS condition_code,
COUNT(DISTINCT NULLIF(BTRIM(s.value_text),'000'))>1 AS conflict
FROM jv_data.races r JOIN jv_data.source_field_values s
 ON s.record_type='RA' AND s.natural_key={source_race_key_sql("r.race_id")}
 AND s.field_name IN ('競走条件コード 2歳条件','競走条件コード 3歳条件','競走条件コード 4歳条件',
 '競走条件コード 5歳以上条件','競走条件コード 最若年条件')
WHERE r.race_date>=DATE '2010-01-01' AND r.race_date<CAST(:end AS date)
 AND r.venue_code BETWEEN '01' AND '10'
GROUP BY r.race_id,s.field_name ORDER BY r.race_id,s.field_name"""

TRAINING_FIELDS = """horse_id,training_date,training_type,training_center_code,course_code,
four_furlongs_seconds,six_furlongs_seconds,lap_400_200_seconds,lap_200_0_seconds,quality_status"""
TRAINING_SQL = f"""SELECT {TRAINING_FIELDS} FROM jv_data.training_sessions
WHERE training_date>=DATE '2010-01-01' AND training_date<CAST(:end AS date)
ORDER BY horse_id,training_date,source_sequence"""
TRAINING_REFERENCE_SQL = """SELECT training_date,training_type,training_center_code,COALESCE(course_code,'') AS course_code,
 COUNT(*) FILTER (WHERE four_furlongs_seconds BETWEEN 35 AND 80) AS n4,
 COALESCE(SUM(four_furlongs_seconds) FILTER (WHERE four_furlongs_seconds BETWEEN 35 AND 80),0)::double precision AS sum4,
 COALESCE(SUM(POWER(four_furlongs_seconds,2)) FILTER (WHERE four_furlongs_seconds BETWEEN 35 AND 80),0)::double precision AS sq4,
 COUNT(*) FILTER (WHERE lap_200_0_seconds BETWEEN 8 AND 25) AS n1,
 COALESCE(SUM(lap_200_0_seconds) FILTER (WHERE lap_200_0_seconds BETWEEN 8 AND 25),0)::double precision AS sum1,
 COALESCE(SUM(POWER(lap_200_0_seconds,2)) FILTER (WHERE lap_200_0_seconds BETWEEN 8 AND 25),0)::double precision AS sq1
 FROM jv_data.training_sessions WHERE training_date>=DATE '2009-01-01' AND training_date<CAST(:end AS date)
 GROUP BY training_date,training_type,training_center_code,COALESCE(course_code,'')
 ORDER BY training_date,training_type,training_center_code,course_code"""
