"""発表オッズと単勝払戻の研究用読み取り。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

from jvdata_bridge_mcp.config import JvDataSettings
from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.ml._development import archive_directory
from jvdata_bridge_mcp.ml.research.data import write_query
from jvdata_bridge_mcp.tools._observation_scope import now_jst

MARKET_SQL = """WITH candidates AS MATERIALIZED (
 SELECT o.*,r.scheduled_start_at,r.scheduled_start_at-INTERVAL '10 minutes' AS decision_at
 FROM jv_data.win_place_odds_observations o JOIN jv_data.races r ON r.race_id=o.race_id
 WHERE o.race_date>=CAST(:start AS date) AND o.race_date<CAST(:end AS date)
 AND o.data_stage_code IN ('1','2') AND o.published_at<=r.scheduled_start_at-INTERVAL '10 minutes'
 AND r.venue_code BETWEEN '01' AND '10'
), ranked AS MATERIALIZED (
 SELECT DISTINCT race_id,observation_key,
 DENSE_RANK() OVER (PARTITION BY race_id ORDER BY published_at DESC,source_sequence DESC) AS priority
 FROM candidates
), snapshots AS MATERIALIZED (
 SELECT race_id,MIN(observation_key) AS observation_key FROM ranked WHERE priority=1
 GROUP BY race_id HAVING COUNT(DISTINCT observation_key)=1
)
SELECT c.* FROM candidates c JOIN snapshots s ON s.race_id=c.race_id AND s.observation_key=c.observation_key
ORDER BY c.race_id,c.horse_number"""


async def extract_market(database: JvDataDatabase, output: Path, start: date, end: date) -> None:
    output = archive_directory(output)
    if not start < end <= now_jst().date():
        raise ValueError("market range must be nonempty and end by today")
    parameters = {"start": start.isoformat(), "end": end.isoformat()}
    await write_query(database, MARKET_SQL, output / "market.parquet", parameters)
    await write_query(
        database,
        """SELECT race_id,combination,payout_amount_yen,is_refund,is_special_payout,is_not_established
        FROM jv_mart.payouts WHERE race_date>=CAST(:start AS date) AND race_date<CAST(:end AS date) AND bet_type='win'""",
        output / "win-payouts.parquet",
        parameters,
    )
    (output / "market-query.sql").write_text(MARKET_SQL, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    args = parser.parse_args()

    async def run() -> None:
        database = JvDataDatabase(JvDataSettings.from_env())
        try:
            await extract_market(database, args.output, args.start_date, args.end_date)
        finally:
            await database.dispose()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())


if __name__ == "__main__":
    main()
