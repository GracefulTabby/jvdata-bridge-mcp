"""現行SQLを同日レースへまとめて適用し、近似ベースラインを使わず比較する。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from jvdata_bridge_mcp.config import JvDataSettings
from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.ml._development import archive_directory, write_json
from jvdata_bridge_mcp.tools._observation_scope import now_jst
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.prediction._indicator_sql import indicator_feature_ctes


def baseline_batch_sql() -> str:
    sql = indicator_feature_ctes("r.race_date")
    sql = sql.replace("SELECT\n        r.race_date,", "SELECT\n        r.race_id, r.race_date,", 1)
    sql = sql.replace("WHERE r.race_id = :race_code", "WHERE r.race_id = ANY(CAST(:race_codes AS text[]))")
    sql = sql.replace("SELECT\n        c.horse_number,", "SELECT\n        c.race_id, c.horse_number,", 1)
    sql = sql.replace(
        "CROSS JOIN target AS t\n    WHERE c.race_id = :race_code", "JOIN target AS t ON t.race_id=c.race_id"
    )
    sql = sql.replace("SELECT\n        cards.horse_id,", "SELECT\n        cards.race_id, cards.horse_id,", 1)
    sql = sql.replace("stored.race_id = :race_code", "stored.race_id = cards.race_id")
    sql = sql.replace("speed.horse_id = c.horse_id", "speed.horse_id = c.horse_id AND speed.race_id=c.race_id")
    sql = sql.replace("PERCENT_RANK() OVER (ORDER BY", "PERCENT_RANK() OVER (PARTITION BY f.race_id ORDER BY")
    from jvdata_bridge_mcp.tools.prediction._indicator_sql import DEFAULT_INDICATOR_WEIGHTS, SPEED_QUALITY_WEIGHT

    terms = [f"{k.removesuffix('_weight')}_score * {v}" for k, v in DEFAULT_INDICATOR_WEIGHTS.items()]
    terms.append(f"speed_quality_score * {SPEED_QUALITY_WEIGHT}")
    return (
        sql
        + ", scored AS (SELECT *, "
        + " + ".join(terms)
        + " AS utility FROM component_scores)\n"
        + """
    SELECT race_id,horse_id,horse_number,utility,
      EXP(utility)/SUM(EXP(utility)) OVER (PARTITION BY race_id) AS win_probability
    FROM scored ORDER BY race_id,horse_number
    """
    )


async def extract_baseline(database: JvDataDatabase, features: Path, output: Path, start: date, end: date) -> Path:
    import pandas as pd

    output = archive_directory(output)
    if not start < end <= now_jst().date():
        raise ValueError("comparison range must be nonempty and end by today")
    frame = pd.read_parquet(features, columns=["race_id", "race_date", "horse_id"])
    dates = frame[(frame.race_date >= start.isoformat()) & (frame.race_date < end.isoformat())].groupby(
        "race_date", sort=True
    )
    all_rows: list[dict[str, Any]] = []
    sql = baseline_batch_sql()
    query_hash = hashlib.sha256(sql.encode()).hexdigest()
    for race_day, daily in dates:
        race_ids = sorted(daily.race_id.unique().tolist())
        identity = {"query_sha256": query_hash, "races": race_ids}
        cache = output / f"baseline-{race_day}.json"
        if cache.exists():
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved["identity"] != identity:
                raise ValueError("cached baseline query or roster changed")
            rows = saved["rows"]
        else:
            rows = await fetch_rows(database, sql, {"race_codes": race_ids}, statement_timeout_seconds=600)
            write_json(cache, {"identity": identity, "rows": rows})
        all_rows.extend(rows)
        print("exact baseline", race_day, len(rows), flush=True)
    if not all_rows:
        raise ValueError("no baseline predictions in the requested range")
    path = output / "exact-baseline.parquet"
    pd.DataFrame(all_rows).to_parquet(path, index=False)
    (output / "exact-baseline.sql").write_text(sql, encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("features", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("start-date", "end-date"):
        parser.add_argument(f"--{name}", type=date.fromisoformat, required=True)
    args = parser.parse_args()

    async def run() -> None:
        database = JvDataDatabase(JvDataSettings.from_env())
        try:
            await extract_baseline(database, args.features, args.output, args.start_date, args.end_date)
        finally:
            await database.dispose()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())


if __name__ == "__main__":
    main()
