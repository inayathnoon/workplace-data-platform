"""Load ``data/raw/`` into the DuckDB ``raw`` schema, under contract.

Two-step on purpose:

1. Validate with Pandera in pandas, partition by partition. This is the slow,
   careful step, and on the full profile it samples partitions rather than
   reading 35M rows through pandas.
2. Load with DuckDB's native Parquet reader, which never materialises the data
   in Python at all.

Doing the load in DuckDB and the validation in pandas keeps the contract honest
without paying pandas' memory cost for the whole feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from ..config import RAW_DIR, WAREHOUSE_PATH, Config, load_config
from ..contracts import SOURCE_SCHEMAS

# Sources written as a single file rather than daily partitions.
UNPARTITIONED = {"workplace_dim", "leave_requests", "travel_bookings", "dept_schedule"}

# Above this many partitions, validate a sample instead of all of them. The
# contract is about schema shape, which does not vary day to day; the daily
# variation that does matter is the DQ layer's job.
MAX_FULLY_VALIDATED_PARTITIONS = 30


@dataclass
class LoadResult:
    table: str
    rows: int
    partitions: int
    partitions_validated: int


def _partitions(source: str) -> list[Path]:
    root = RAW_DIR / source
    if not root.exists():
        raise FileNotFoundError(f"No raw data for {source!r}. Run `make data` first.")
    if source in UNPARTITIONED:
        return sorted(root.glob("*.parquet"))
    return sorted(root.glob("dt=*/*.parquet"))


def validate_source(source: str, files: list[Path], rng_seed: int = 0) -> int:
    """Validate partitions against the source contract. Returns how many."""
    schema = SOURCE_SCHEMAS[source]
    if len(files) > MAX_FULLY_VALIDATED_PARTITIONS:
        step = len(files) // MAX_FULLY_VALIDATED_PARTITIONS
        chosen = files[::step][:MAX_FULLY_VALIDATED_PARTITIONS]
    else:
        chosen = files
    for path in chosen:
        frame = pd.read_parquet(path)
        schema.validate(frame, lazy=True)
    return len(chosen)


def load_raw(cfg: Config | None = None, validate: bool = True) -> list[LoadResult]:
    cfg = cfg or load_config()
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)

    results: list[LoadResult] = []
    con = duckdb.connect(str(WAREHOUSE_PATH))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS raw")
        for source in SOURCE_SCHEMAS:
            files = _partitions(source)
            validated = validate_source(source, files) if validate else 0

            glob = (
                str(RAW_DIR / source / "*.parquet")
                if source in UNPARTITIONED
                else str(RAW_DIR / source / "dt=*/*.parquet")
            )
            con.execute(f"DROP TABLE IF EXISTS raw.{source}")
            con.execute(
                f"CREATE TABLE raw.{source} AS "
                f"SELECT * FROM read_parquet('{glob}', union_by_name = true)"
            )
            row = con.execute(f"SELECT count(*) FROM raw.{source}").fetchone()
            assert row is not None  # a scalar aggregate always returns one row
            rows = row[0]
            results.append(LoadResult(source, int(rows), len(files), validated))
    finally:
        con.close()
    return results


if __name__ == "__main__":  # pragma: no cover
    for r in load_raw():
        print(
            f"raw.{r.table:24s} {r.rows:>12,} rows  "
            f"({r.partitions} partitions, {r.partitions_validated} validated)"
        )
