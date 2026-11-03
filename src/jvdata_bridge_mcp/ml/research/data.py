"""公式条件と調教母集団をread-onlyで抽出する研究用CLI。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

from jvdata_bridge_mcp.config import JvDataSettings
from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.ml._development import archive_directory, write_json
from jvdata_bridge_mcp.ml._reference_sql import CLASS_SQL, TRAINING_REFERENCE_SQL, TRAINING_SQL
from jvdata_bridge_mcp.tools._observation_scope import now_jst


async def write_query(database: JvDataDatabase, sql: str, path: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    identity = hashlib.sha256((sql + json.dumps(parameters, sort_keys=True)).encode()).hexdigest()
    metadata = path.with_suffix(".json")
    if metadata.exists():
        saved = json.loads(metadata.read_bytes())
        if saved["query_sha256"] != identity or saved["sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("existing extraction identity changed")
        return saved
    if path.exists():
        raise ValueError("unmanifested extraction must not be overwritten")
    writer, count = None, 0
    partial = path.with_suffix(".partial.parquet")
    try:
        async with database.read_connection(statement_timeout_seconds=600) as connection:
            assert (await connection.execute(text("SHOW transaction_read_only"))).scalar() == "on"
            result = await connection.stream(text(sql), parameters)
            async for chunk in result.mappings().partitions(50000):
                records = [{k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()} for row in chunk]
                table = pa.Table.from_pylist(records)
                if writer is None:
                    fields = [
                        pa.field(field.name, pa.float64() if field.name.endswith("_seconds") else pa.string())
                        if pa.types.is_null(field.type)
                        else field
                        for field in table.schema
                    ]
                    writer = pq.ParquetWriter(partial, pa.schema(fields))
                writer.write_table(table.cast(writer.schema))
                count += len(chunk)
                if count % 500000 == 0:
                    print(path.stem, count, flush=True)
    finally:
        if writer is not None:
            writer.close()
    if not count:
        raise ValueError("empty extended extraction")
    partial.replace(path)
    saved = {"query_sha256": identity, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "rows": count}
    write_json(metadata, saved)
    print("extracted", path.stem, count, flush=True)
    return saved


async def extract(output: Path, end: date) -> None:
    output = archive_directory(output)
    if end > now_jst().date():
        raise ValueError("extract end must not exceed today")
    database = JvDataDatabase(JvDataSettings.from_env())
    try:
        records = {}
        for name, statement in (
            ("classes", CLASS_SQL),
            ("training", TRAINING_SQL),
            ("training-reference", TRAINING_REFERENCE_SQL),
        ):
            records[name] = await write_query(
                database, statement, output / f"{name}.parquet", {"end": end.isoformat()}
            )
        write_json(
            output / "extraction.json",
            {
                "read_only": True,
                "end_exclusive": end,
                "extracts": records,
                "class_conflicts": "null; artifact hashes never determine time or precedence",
                "historical_card_publication_versions": "unverified",
            },
        )
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(extract(args.output, args.end_date))


if __name__ == "__main__":
    main()
