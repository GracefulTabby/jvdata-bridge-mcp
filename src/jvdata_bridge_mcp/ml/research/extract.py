"""既存read modelからローカルarchiveへ抽出する。DBのDDL/DMLは行わない。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from jvdata_bridge_mcp.config import JvDataSettings
from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.ml._development import archive_directory, write_json
from jvdata_bridge_mcp.tools._observation_scope import now_jst

EXTRACTS = {
    "runs": """SELECT c.race_id,c.race_date,c.horse_id,c.horse_number,c.horse_age,c.sex_code,c.jockey_id,
    c.trainer_id,c.assigned_weight_kg,c.frame_number,r.venue_code,r.track_code,r.distance_meters,r.grade_code,
    r.starters,r.finishers,r.scheduled_start_at,r.first_three_furlongs_seconds,r.turf_condition_code,r.dirt_condition_code,
    x.finish_position,x.elapsed_seconds,x.time_difference_seconds,x.last_three_furlongs_seconds,
    x.final_win_odds,x.final_win_popularity,x.corner_1_position,x.corner_2_position,x.corner_3_position,x.corner_4_position,
    x.running_style_code,x.abnormality_code,x.is_starter,x.is_finisher,x.is_final_result,x.is_complete_result,
    x.dead_heat_code,x.dead_heat_count
    FROM jv_mart.prediction_race_cards c JOIN jv_data.races r ON r.race_id=c.race_id
    LEFT JOIN jv_data.race_results x ON x.race_id=c.race_id AND x.horse_id=c.horse_id
    WHERE c.race_date >= DATE '2010-01-01' AND c.race_date < CAST(:end_date AS date)
      AND r.venue_code BETWEEN '01' AND '10'
    ORDER BY c.race_date,c.race_id,c.horse_number""",
    "speed": """SELECT * FROM jv_mart.horse_speed_quality
    WHERE race_date >= DATE '2018-01-01' AND race_date < CAST(:end_date AS date)
      AND model_version='temporal-linear-v3-speed-2026-holdout'""",
    "training": """SELECT horse_id,training_date,training_time,training_center_code,training_type,
    four_furlongs_seconds,six_furlongs_seconds,lap_200_0_seconds,quality_status
    FROM jv_data.training_sessions WHERE training_date >= DATE '2017-12-01'
      AND training_date < CAST(:end_date AS date)""",
}


async def extract(output: Path, end_date: date) -> None:
    """Parquetを年ごとのバッチで書き、調教の全行をメモリへ載せない。"""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    output = archive_directory(output)
    if end_date > now_jst().date():
        raise ValueError("extract end must not exceed today")
    database = JvDataDatabase(JvDataSettings.from_env())
    manifest = {"end_date_exclusive": end_date.isoformat(), "read_only": True, "extracts": {}}
    try:
        for name, statement in EXTRACTS.items():
            target = output / f"{name}.parquet"
            partial = output / f"{name}.partial.parquet"
            if target.exists():
                raise ValueError(f"existing snapshot will not be overwritten: {target}")
            writer = None
            count = 0
            try:
                async with database.read_connection(statement_timeout_seconds=600) as connection:
                    if (await connection.execute(text("SHOW transaction_read_only"))).scalar() != "on":
                        raise ValueError("read-only transaction required")
                    result = await connection.stream(text(statement), {"end_date": end_date.isoformat()})
                    async for chunk in result.mappings().partitions(50000):
                        normalized = [
                            {k: (float(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in chunk
                        ]
                        data = pd.DataFrame(normalized)
                        if "training_time" in data:
                            data["training_time"] = data["training_time"].map(
                                lambda value: str(value) if value is not None else None
                            )
                        table = pa.Table.from_pandas(data, preserve_index=False)
                        if writer is None:
                            numeric = {
                                "horse_age",
                                "horse_number",
                                "frame_number",
                                "assigned_weight_kg",
                                "distance_meters",
                                "starters",
                                "finishers",
                                "first_three_furlongs_seconds",
                                "finish_position",
                                "elapsed_seconds",
                                "time_difference_seconds",
                                "last_three_furlongs_seconds",
                                "final_win_odds",
                                "final_win_popularity",
                                "corner_1_position",
                                "corner_2_position",
                                "corner_3_position",
                                "corner_4_position",
                                "dead_heat_count",
                                "four_furlongs_seconds",
                                "six_furlongs_seconds",
                                "lap_200_0_seconds",
                                "speed_quality_raw",
                                "speed_quality_coverage",
                                "speed_observation_count",
                            }
                            schema = pa.schema(
                                [
                                    pa.field(f.name, pa.float64() if f.name in numeric else pa.string())
                                    if pa.types.is_null(f.type)
                                    else f
                                    for f in table.schema
                                ]
                            )
                            writer = pq.ParquetWriter(partial, schema)
                        writer.write_table(table.cast(writer.schema))
                        count += len(chunk)
            finally:
                if writer is not None:
                    writer.close()
            if not count:
                raise ValueError(f"no records in {name}; data coverage is insufficient")
            partial.replace(target)
            manifest["extracts"][name] = {
                "query": statement,
                "rows": count,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
            print(name, count, flush=True)
        write_json(output / "extraction.json", manifest)
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="read-only model-development extraction")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, default=now_jst().date())
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(extract(args.output, args.end_date))


if __name__ == "__main__":
    main()
