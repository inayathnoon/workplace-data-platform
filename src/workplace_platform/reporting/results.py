"""The results table `make demo` prints.

Its job is to answer one question: did the pipeline recover what the simulator
planted? Everything else in the output is context for that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import duckdb
import pandas as pd

from ..config import DEFECT_MANIFEST, GROUND_TRUTH, WAREHOUSE_PATH, Config, load_config
from ..dq.runner import defect_recall, render_defect_recall, run_checks
from ..semantic.compile import compile_metric
from ..semantic.registry import load_registry

LAYERS = {
    "raw": [
        "hr_employee_snapshot",
        "badge_taps",
        "leave_requests",
        "travel_bookings",
        "workplace_dim",
        "dept_schedule",
    ],
    "main_staging": [
        "stg_hr_employee_snapshot",
        "stg_badge_taps",
        "stg_leave_requests",
        "stg_travel_bookings",
        "stg_workplace_space",
        "stg_workplace",
        "stg_dept_schedule",
        "stg_city_primary_workplace",
    ],
    "main_intermediate": [
        "int_employee_scd2",
        "int_leave_days",
        "int_travel_days",
        "int_tap_day",
        "int_employee_day",
    ],
    "main_marts": [
        "dim_date",
        "dim_employee",
        "dim_workplace",
        "fct_attendance_daily",
        "fct_workplace_capacity_daily",
        "fct_floor_presence_hourly",
    ],
}


@dataclass
class Results:
    row_counts: pd.DataFrame
    attendance: pd.DataFrame
    metrics: pd.DataFrame
    defects: list[dict]
    catalogue_size: int
    dq_summary: str


def _row_counts(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    rows = []
    for schema, tables in LAYERS.items():
        for table in tables:
            row = con.execute(f"select count(*) from {schema}.{table}").fetchone()
            assert row is not None
            count = row[0]
            rows.append({"layer": schema, "table": table, "rows": int(count)})
    return pd.DataFrame(rows)


def attendance_recovery(con: duckdb.DuckDBPyConnection, cfg: Config) -> pd.DataFrame:
    """Planted versus recovered attendance rate, per region.

    'Planted' is the rate the simulator actually realised, not the rate
    requested in the config: eligibility, leave and travel move the realised
    rate away from the target, and comparing against the target would report
    simulator behaviour as pipeline error.
    """
    registry = load_registry()
    compiled = compile_metric(registry, "attendance_rate", ["region"], None, cfg=cfg)
    recovered = con.execute(compiled.sql).df()

    truth = json.loads(GROUND_TRUTH.read_text())
    planted = truth["planted"]["attendance_rate_by_region_realised"]
    configured = truth["planted"]["attendance_rate_by_region_configured"]

    recovered["planted"] = recovered["region"].map(planted)
    recovered["configured"] = recovered["region"].map(configured)
    recovered = recovered.rename(columns={"attendance_rate": "recovered"})
    recovered["abs_error"] = (recovered["recovered"] - recovered["planted"]).abs()
    return recovered[["region", "configured", "planted", "recovered", "abs_error"]].sort_values(
        "region"
    )


def metric_snapshot(con: duckdb.DuckDBPyConnection, cfg: Config) -> pd.DataFrame:
    """One headline value per metric, computed through the compiler."""
    registry = load_registry()
    rows = []
    for metric in sorted(registry, key=lambda m: m.name):
        grain = "week" if "week" in metric.time_grain else metric.time_grain[0]
        try:
            compiled = compile_metric(registry, metric.name, [], grain, cfg=cfg)
            frame = con.execute(compiled.sql).df()
            value = float(frame[metric.name].mean()) if not frame.empty else float("nan")
        except Exception as exc:  # pragma: no cover - surfaced in the table
            rows.append({"metric": metric.name, "grain": grain, "value": None, "note": str(exc)})
            continue
        rows.append({"metric": metric.name, "grain": grain, "value": round(value, 4), "note": ""})
    return pd.DataFrame(rows)


def collect(cfg: Config | None = None) -> Results:
    cfg = cfg or load_config()
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        counts = _row_counts(con)
        attendance = attendance_recovery(con, cfg)
        metrics = metric_snapshot(con, cfg)
    finally:
        con.close()

    card = run_checks(cfg)
    counts_by_status = card.counts
    summary = (
        f"{len(card.results)} checks: {counts_by_status['pass']} pass, "
        f"{counts_by_status['warn']} warn, {counts_by_status['fail']} fail "
        f"({len(card.unexpected_failures)} unexpected)"
    )
    return Results(
        row_counts=counts,
        attendance=attendance,
        metrics=metrics,
        defects=defect_recall(card),
        catalogue_size=len(load_registry()),
        dq_summary=summary,
    )


def render(results: Results, cfg: Config) -> str:
    out: list[str] = []
    rule = "=" * 78
    out.append(rule)
    out.append(f"workplace-data-platform  |  profile: {cfg.profile_name}  |  seed: {cfg.seed}")
    out.append(f"window: {cfg.start_date} to {cfg.end_date}")
    out.append(rule)

    out.append("\nROW COUNTS BY LAYER")
    for layer, group in results.row_counts.groupby("layer", sort=False):
        out.append(f"  {layer}  ({group['rows'].sum():,} rows)")
        for _, row in group.iterrows():
            out.append(f"    {row['table']:34s} {row['rows']:>12,}")

    out.append("\nPLANTED VS RECOVERED - attendance rate by region")
    out.append(
        f"  {'region':8s} {'configured':>11s} {'planted':>9s} {'recovered':>10s} {'abs err':>9s}"
    )
    for _, row in results.attendance.iterrows():
        out.append(
            f"  {row['region']:8s} {row['configured']:>11.4f} {row['planted']:>9.4f} "
            f"{row['recovered']:>10.4f} {row['abs_error']:>9.5f}"
        )
    worst = results.attendance["abs_error"].max()
    out.append(f"  worst absolute error: {worst:.5f}")

    out.append(f"\nMETRIC CATALOGUE  ({results.catalogue_size} metrics, all compiled)")
    for _, row in results.metrics.iterrows():
        value = "n/a" if row["value"] is None else f"{row['value']:,.4f}"
        note = f"  {row['note']}" if row["note"] else ""
        out.append(f"  {row['metric']:38s} {row['grain']:5s} {value:>14s}{note}")

    out.append("\nDATA QUALITY")
    out.append(f"  {results.dq_summary}")
    out.append("\nDEFECT RECALL - did the checks find what the generator planted?")
    out.extend("  " + line for line in render_defect_recall(results.defects).splitlines())

    caught = sum(1 for d in results.defects if d["caught"])
    out.append(f"\n  {caught}/{len(results.defects)} planted defect classes detected")
    out.append(rule)
    return "\n".join(out)


def main() -> int:
    cfg = load_config()
    if not WAREHOUSE_PATH.exists():
        print("No warehouse found. Run `make pipeline` first.")
        return 1
    if not DEFECT_MANIFEST.exists():
        print("No defect manifest found. Run `make data` first.")
        return 1
    results = collect(cfg)
    print(render(results, cfg))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
