"""Dagster asset graph for the whole platform.

The assets are thin: each one calls a function that already works standalone and
is already tested. That is deliberate. Orchestration should be able to fail
without taking the pipeline's logic down with it, and every step here can be run
from the Makefile or a REPL without Dagster present.

Asset checks are attached to the three tables everything else depends on. They
are checks, not tests: they run against the materialised asset on every run, and
a failure blocks downstream materialisation rather than failing a build.
"""

# NOTE: no `from __future__ import annotations` in this module. Dagster
# resolves the `context` parameter's type at decoration time, and postponed
# annotations turn it into a string it cannot match.

import subprocess

import duckdb
from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    AssetExecutionContext,
    AssetSelection,
    Definitions,
    MetadataValue,
    Output,
    ScheduleDefinition,
    asset,
    asset_check,
    define_asset_job,
)

from ..config import DBT_DIR, WAREHOUSE_PATH, load_config
from ..dq.runner import defect_recall, render_defect_recall, render_scorecard, run_checks
from ..gen.run import generate_all
from ..semantic.compile import compile_all
from ..semantic.validate import validate
from ..warehouse.loader import load_raw


@asset(
    group_name="sources",
    description="Five source-system extracts plus the office-day policy, written to data/raw/.",
    compute_kind="python",
)
def raw_sources(context: AssetExecutionContext) -> Output[dict]:
    cfg = load_config()
    result = generate_all(cfg)
    counts = result["row_counts"]
    return Output(
        result,
        metadata={
            "profile": cfg.profile_name,
            "rows_total": MetadataValue.int(sum(counts.values())),
            "row_counts": MetadataValue.json(counts),
            "defects_injected": MetadataValue.json(result["defects"]),
        },
    )


@asset(
    group_name="warehouse",
    deps=[raw_sources],
    description="Raw extracts loaded into DuckDB under Pandera contracts.",
    compute_kind="duckdb",
)
def warehouse_raw(context: AssetExecutionContext) -> Output[dict]:
    results = load_raw()
    rows = {r.table: r.rows for r in results}
    return Output(
        rows,
        metadata={
            "tables": MetadataValue.int(len(results)),
            "rows": MetadataValue.json(rows),
            "partitions_validated": MetadataValue.json(
                {r.table: r.partitions_validated for r in results}
            ),
        },
    )


@asset(
    group_name="warehouse",
    deps=[warehouse_raw],
    description="dbt staging, intermediate and mart models, with their tests.",
    compute_kind="dbt",
)
def dbt_models(context: AssetExecutionContext) -> Output[dict]:
    """Invoked as a subprocess rather than through dagster-dbt.

    dagster-dbt would give per-model assets in the UI, which is nicer, at the
    cost of a dependency that pins both tools together. For a repo whose point
    is the modelling rather than the orchestration, the subprocess is the
    honest trade: the same command a developer runs by hand.
    """
    proc = subprocess.run(
        ["dbt", "build", "--profiles-dir", "."],
        cwd=DBT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    tail = "\n".join(proc.stdout.strip().splitlines()[-25:])
    if proc.returncode != 0:
        context.log.error(tail)
        raise RuntimeError(f"dbt build failed with exit code {proc.returncode}")
    return Output(
        {"returncode": proc.returncode},
        metadata={"dbt_output": MetadataValue.md(f"```\n{tail}\n```")},
    )


@asset(
    group_name="semantic",
    deps=[dbt_models],
    description="Metric registry compiled to SQL, catalogue and lineage graph.",
    compute_kind="python",
)
def semantic_layer(context: AssetExecutionContext) -> Output[dict]:
    result = compile_all()
    report = validate()
    if not report.ok:
        context.log.error(report.render())
        raise RuntimeError("metric registry does not agree with the warehouse")
    return Output(
        {"metrics": result["metrics"]},
        metadata={
            "metrics": MetadataValue.int(result["metrics"]),
            "catalogue": MetadataValue.path(str(result["catalogue"])),
            "validation": MetadataValue.md(f"```\n{report.render()}\n```"),
        },
    )


@asset(
    group_name="quality",
    deps=[dbt_models],
    description="Data quality scorecard and recall against the planted defects.",
    compute_kind="python",
)
def dq_scorecard(context: AssetExecutionContext) -> Output[dict]:
    card = run_checks()
    recall = defect_recall(card)
    render_scorecard(card)
    counts = card.counts
    if not card.passed:
        context.log.error(card.render())
    return Output(
        {"counts": counts, "recall": recall},
        metadata={
            "pass": MetadataValue.int(counts["pass"]),
            "warn": MetadataValue.int(counts["warn"]),
            "fail": MetadataValue.int(counts["fail"]),
            "unexpected_failures": MetadataValue.int(len(card.unexpected_failures)),
            "scorecard": MetadataValue.md(f"```\n{card.render()}\n```"),
            "defect_recall": MetadataValue.md(f"```\n{render_defect_recall(recall)}\n```"),
        },
    )


# --- Asset checks ----------------------------------------------------------
#
# One per critical mart. Each asserts the property that, if it broke, would make
# every number built on that table wrong without anything visibly failing.


def _scalar(sql: str) -> float:
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        row = con.execute(sql).fetchone()
        return float((row[0] if row else 0) or 0)
    finally:
        con.close()


@asset_check(asset=dbt_models, name="int_employee_day_one_row_per_employee_day", blocking=True)
def check_employee_day_grain() -> AssetCheckResult:
    duplicates = _scalar(
        """
        select count(*) from (
            select emp_id, local_date
            from main_intermediate.int_employee_day
            group by 1, 2 having count(*) > 1
        )
        """
    )
    return AssetCheckResult(
        passed=duplicates == 0,
        severity=AssetCheckSeverity.ERROR,
        metadata={
            "duplicate_keys": MetadataValue.int(int(duplicates)),
            "why": MetadataValue.text(
                "A duplicated employee-day inflates every attendance count downstream, and does "
                "it quietly - the numbers stay plausible."
            ),
        },
    )


@asset_check(asset=dbt_models, name="fct_attendance_daily_within_population", blocking=True)
def check_attendance_within_population() -> AssetCheckResult:
    violations = _scalar(
        """
        select count(*) from main_marts.fct_attendance_daily
        where attendance_expected_met > attendance_expected_days
           or attendance_daily > employee_days
        """
    )
    return AssetCheckResult(
        passed=violations == 0,
        severity=AssetCheckSeverity.ERROR,
        metadata={
            "violating_rows": MetadataValue.int(int(violations)),
            "why": MetadataValue.text(
                "Attendance above the population it is drawn from is the signature of a fan-out "
                "join, usually from overlapping leave requests or undeduplicated taps."
            ),
        },
    )


@asset_check(asset=dbt_models, name="fct_workplace_capacity_daily_supply_identity", blocking=True)
def check_capacity_identity() -> AssetCheckResult:
    violations = _scalar(
        """
        select count(*) from main_marts.fct_workplace_capacity_daily
        where workstation_waste != available_workstations - attendance_actual
           or delivered_workstations != allocated_workstations + unallocated_workstations
        """
    )
    return AssetCheckResult(
        passed=violations == 0,
        severity=AssetCheckSeverity.ERROR,
        metadata={
            "violating_rows": MetadataValue.int(int(violations)),
            "why": MetadataValue.text(
                "These are definitions, not measurements. Drift means a second definition has "
                "appeared somewhere in the graph."
            ),
        },
    )


@asset_check(asset=dq_scorecard, name="no_unexpected_dq_failures", blocking=False)
def check_no_unexpected_dq_failures() -> AssetCheckResult:
    card = run_checks()
    unexpected = card.unexpected_failures
    return AssetCheckResult(
        passed=not unexpected and not card.stale_expectations,
        severity=AssetCheckSeverity.WARN,
        metadata={
            "unexpected": MetadataValue.json([r.name for r in unexpected]),
            "stale_expectations": MetadataValue.json(card.stale_expectations),
        },
    )


full_refresh_job = define_asset_job(
    name="full_refresh",
    selection=AssetSelection.all(),
    description="Generate the sources, rebuild the warehouse, compile metrics, score quality.",
)

daily_schedule = ScheduleDefinition(
    name="daily_refresh",
    job=full_refresh_job,
    # 05:30 UTC: after the badge feed's late-arriving rows have landed, and
    # before the first APAC working day would read yesterday's numbers.
    cron_schedule="30 5 * * *",
    execution_timezone="UTC",
)

defs = Definitions(
    assets=[raw_sources, warehouse_raw, dbt_models, semantic_layer, dq_scorecard],
    asset_checks=[
        check_employee_day_grain,
        check_attendance_within_population,
        check_capacity_identity,
        check_no_unexpected_dq_failures,
    ],
    jobs=[full_refresh_job],
    schedules=[daily_schedule],
)
