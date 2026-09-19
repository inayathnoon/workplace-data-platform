"""The checks themselves.

Ordered roughly by how early they would catch a problem: is the data here, does
it join, is it the right shape, does it describe a possible world, has it
shifted, does it agree with itself.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from .base import Category, CheckResult, Status, check, grade, query

SAMPLE_ROWS = 10


# --- 1. Freshness ----------------------------------------------------------


@check(
    "freshness_by_source",
    Category.FRESHNESS,
    "Each source has arrived within its agreed lateness window.",
)
def freshness_by_source(con, cfg: Config) -> list[CheckResult]:
    """Lateness measured against the simulated window end, not wall-clock time.

    The badge feed has a deliberately generous SLA because 3% of its rows are
    reported a day late by design. Holding it to the same window as the HR feed
    would produce a check that fails every single day and is therefore useless.
    """
    results: list[CheckResult] = []
    sources = {
        "hr_employee_snapshot": "select max(snapshot_date) as latest from raw.hr_employee_snapshot",
        "badge_taps": "select max(ingested_date) as latest from raw.badge_taps",
        "leave_requests": "select max(cast(applied_ts as date)) as latest from raw.leave_requests",
        "travel_bookings": "select max(depart_date) as latest from raw.travel_bookings",
        "workplace_dim": None,  # reference data, no event date
    }
    for source, sql in sources.items():
        sla_hours = cfg.dq.freshness_sla_hours[source]
        if sql is None:
            results.append(
                CheckResult(
                    name=f"freshness_{source}",
                    category=Category.FRESHNESS,
                    status=Status.PASS,
                    explanation=f"{source} is reference data with no event date; not aged.",
                    value=0.0,
                    threshold=float(sla_hours),
                )
            )
            continue
        latest = query(con, sql)["latest"].iloc[0]
        if latest is None:
            results.append(
                CheckResult(
                    name=f"freshness_{source}",
                    category=Category.FRESHNESS,
                    status=Status.FAIL,
                    explanation=f"{source} is empty - nothing has been delivered at all.",
                )
            )
            continue
        # Clamped at zero: the badge feed's late-arrival flag pushes its
        # ingested date one day past the window end, which is early, not stale.
        lag_hours = max((cfg.end_date - pd.to_datetime(latest).date()).days * 24, 0)
        status = grade(lag_hours, warn=sla_hours * 0.75, fail=sla_hours)
        results.append(
            CheckResult(
                name=f"freshness_{source}",
                category=Category.FRESHNESS,
                status=status,
                explanation=(
                    f"{source} is {lag_hours}h behind the reporting date "
                    f"(agreed limit {sla_hours}h)."
                ),
                value=float(lag_hours),
                threshold=float(sla_hours),
            )
        )
    return results


# --- 2. Referential integrity ---------------------------------------------


@check(
    "referential_integrity",
    Category.INTEGRITY,
    "Facts join to their dimensions with no orphans.",
)
def referential_integrity(con, cfg: Config) -> list[CheckResult]:
    pairs = [
        ("fct_attendance_daily", "workplace_code", "dim_workplace", "workplace_code"),
        ("fct_workplace_capacity_daily", "workplace_code", "dim_workplace", "workplace_code"),
        ("fct_floor_presence_hourly", "workplace_code", "dim_workplace", "workplace_code"),
        ("fct_attendance_daily", "local_date", "dim_date", "date_day"),
    ]
    results = []
    for fact, fk, dim, pk in pairs:
        orphans = query(
            con,
            f"""
            select f.{fk} as key, count(*) as rows
            from main_marts.{fact} f
            left join main_marts.{dim} d on f.{fk} = d.{pk}
            where d.{pk} is null
            group by 1 order by rows desc limit {SAMPLE_ROWS}
            """,
        )
        total = int(orphans["rows"].sum()) if not orphans.empty else 0
        results.append(
            CheckResult(
                name=f"integrity_{fact}_{fk}",
                category=Category.INTEGRITY,
                status=Status.PASS if total == 0 else Status.FAIL,
                explanation=(
                    f"{fact}.{fk} all resolve to {dim}."
                    if total == 0
                    else f"{total:,} rows in {fact} point at a {dim} row that does not exist."
                ),
                value=float(total),
                threshold=0.0,
                sample=None if orphans.empty else orphans,
            )
        )
    return results


# --- 3. Grain uniqueness ---------------------------------------------------


@check("grain_uniqueness", Category.GRAIN, "Every fact holds exactly one row per declared grain.")
def grain_uniqueness(con, cfg: Config) -> list[CheckResult]:
    grains = {
        "int_employee_day": (["emp_id", "local_date"], "main_intermediate"),
        "fct_attendance_daily": (["local_date", "workplace_code", "dept_l2"], "main_marts"),
        "fct_workplace_capacity_daily": (["local_date", "workplace_code"], "main_marts"),
        "fct_floor_presence_hourly": (
            ["local_date", "workplace_code", "tower", "floor", "local_hour"],
            "main_marts",
        ),
    }
    results = []
    for table, (keys, schema) in grains.items():
        key_sql = ", ".join(keys)
        dupes = query(
            con,
            f"""
            select {key_sql}, count(*) as rows
            from {schema}.{table}
            group by {key_sql}
            having count(*) > 1
            order by rows desc limit {SAMPLE_ROWS}
            """,
        )
        results.append(
            CheckResult(
                name=f"grain_{table}",
                category=Category.GRAIN,
                status=Status.PASS if dupes.empty else Status.FAIL,
                explanation=(
                    f"{table} is unique on ({', '.join(keys)})."
                    if dupes.empty
                    else f"{len(dupes):,} duplicated keys in {table} - every count built on it "
                    "is overstated."
                ),
                value=float(len(dupes)),
                threshold=0.0,
                sample=None if dupes.empty else dupes,
            )
        )
    return results


# --- 4. Status transition legality ----------------------------------------


@check(
    "active_after_termination",
    Category.LEGALITY,
    "Nobody is reported as active after their termination date.",
)
def active_after_termination(con, cfg: Config) -> CheckResult:
    rows = query(
        con,
        """
        select emp_id, term_date, min(snapshot_date) as first_bad_day,
               max(snapshot_date) as last_bad_day, count(*) as bad_rows
        from main_staging.stg_hr_employee_snapshot
        where term_date is not null
          and snapshot_date > term_date
          and employment_status_reported = 'active'
        group by emp_id, term_date
        order by bad_rows desc
        """,
    )
    bad_rows = int(rows["bad_rows"].sum()) if not rows.empty else 0
    return CheckResult(
        name="active_after_termination",
        category=Category.LEGALITY,
        status=Status.PASS if bad_rows == 0 else Status.FAIL,
        explanation=(
            "No employee is reported active after leaving."
            if bad_rows == 0
            else f"{len(rows)} people are still reported as active after their last working "
            f"day, across {bad_rows} daily snapshots. Headcount is overstated and access "
            "may still be live."
        ),
        value=float(bad_rows),
        threshold=0.0,
        sample=rows.head(SAMPLE_ROWS) if not rows.empty else None,
        details={"employees": int(len(rows)), "rows": bad_rows},
    )


@check(
    "snapshot_before_hire_date",
    Category.LEGALITY,
    "Nobody appears as active before their own hire date.",
)
def snapshot_before_hire_date(con, cfg: Config) -> CheckResult:
    rows = query(
        con,
        """
        select emp_id, hire_date, snapshot_date
        from main_staging.stg_hr_employee_snapshot
        where snapshot_date < hire_date
          and employment_status_reported = 'active'
        order by emp_id, snapshot_date
        """,
    )
    n = len(rows)
    return CheckResult(
        name="snapshot_before_hire_date",
        category=Category.LEGALITY,
        status=Status.PASS if n == 0 else Status.FAIL,
        explanation=(
            "No employee is active before their hire date."
            if n == 0
            else f"{n} snapshot rows show someone active before they were hired - a back-dated "
            "record or a hire date corrected after the fact."
        ),
        value=float(n),
        threshold=0.0,
        sample=rows.head(SAMPLE_ROWS) if n else None,
    )


@check(
    "tap_after_termination",
    Category.LEGALITY,
    "No badge activity beyond the grace period after termination.",
)
def tap_after_termination(con, cfg: Config) -> CheckResult:
    """The check a security reviewer asks for first.

    A grace period is allowed because returning a laptop on the Monday after
    you leave is normal. Beyond it, an active badge is an access-revocation
    failure, not a data quality curiosity.
    """
    grace = cfg.dq.tap_after_termination_grace_days
    rows = query(
        con,
        """
        select t.emp_id, e.term_date, min(t.business_date) as first_tap,
               max(t.business_date) as last_tap, count(*) as taps
        from main_staging.stg_badge_taps t
        join main_marts.dim_employee e on t.emp_id = e.emp_id
        where e.term_date is not null
          and t.business_date > e.term_date + cast(? as integer)
        group by t.emp_id, e.term_date
        order by taps desc
        """,
        [grace],
    )
    any_lag = query(
        con,
        """
        select count(*) as taps, count(distinct t.emp_id) as employees
        from main_staging.stg_badge_taps t
        join main_marts.dim_employee e on t.emp_id = e.emp_id
        where e.term_date is not null and t.business_date > e.term_date
        """,
    )

    people = len(rows)
    return CheckResult(
        name="tap_after_termination",
        category=Category.LEGALITY,
        status=Status.PASS if people == 0 else Status.FAIL,
        explanation=(
            f"No badge activity more than {grace} days after termination."
            if people == 0
            else f"{people} people badged in more than {grace} days after their last working "
            "day. Their access was never revoked."
        ),
        value=float(people),
        threshold=0.0,
        sample=rows.head(SAMPLE_ROWS) if people else None,
        details={
            "taps_after_termination_any": int(any_lag["taps"].iloc[0]),
            "employees_after_termination_any": int(any_lag["employees"].iloc[0]),
            "grace_days": grace,
        },
    )


@check(
    "missing_workplace_timezone",
    Category.LEGALITY,
    "Every workplace has a timezone, so local dates are real.",
)
def missing_workplace_timezone(con, cfg: Config) -> CheckResult:
    rows = query(
        con,
        """
        select workplace_code, city, region, delivered_workstations
        from main_marts.dim_workplace
        where has_missing_timezone
        order by delivered_workstations desc
        """,
    )
    n = len(rows)
    affected = int(rows["delivered_workstations"].sum()) if n else 0
    return CheckResult(
        name="missing_workplace_timezone",
        category=Category.LEGALITY,
        status=Status.PASS if n == 0 else Status.FAIL,
        explanation=(
            "Every workplace has a timezone."
            if n == 0
            else f"{n} workplace(s) covering {affected:,} desks have no timezone. Their "
            "attendance has been assigned to UTC days, which may be the wrong day."
        ),
        value=float(n),
        threshold=0.0,
        sample=rows if n else None,
    )


# --- 5. Distribution drift -------------------------------------------------


# Minimum observations per bucket. Below this, PSI reports movement that is
# just sampling noise - a 40-point sample in 10 buckets has four per bucket and
# swings by 0.3 between identical distributions.
MIN_OBSERVATIONS_PER_BUCKET = 8


def _psi(expected: np.ndarray, actual: np.ndarray, buckets: int = 10) -> float:
    """Population Stability Index between two samples.

    Edges come from the reference period, so a shift shows up as mass moving
    between fixed buckets rather than the buckets moving with the data.

    Bucket count is capped by sample size. PSI's weakness is that it happily
    returns a large number from a small sample, and a drift alarm that cries
    wolf on a short window gets muted, which costs more than not having it.
    """
    usable = min(len(expected), len(actual))
    buckets = min(buckets, max(usable // MIN_OBSERVATIONS_PER_BUCKET, 2))
    if len(expected) < buckets or len(actual) == 0:
        return 0.0
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, buckets + 1)))
    if len(edges) < 3:
        return 0.0
    exp_counts, _ = np.histogram(expected, bins=edges)
    act_counts, _ = np.histogram(actual, bins=edges)
    exp_share = np.clip(exp_counts / max(exp_counts.sum(), 1), 1e-6, None)
    act_share = np.clip(act_counts / max(act_counts.sum(), 1), 1e-6, None)
    return float(np.sum((act_share - exp_share) * np.log(act_share / exp_share)))


@check(
    "attendance_rate_drift",
    Category.DRIFT,
    "City attendance rates have not shifted against the trailing baseline.",
)
def attendance_rate_drift(con, cfg: Config) -> CheckResult:
    """PSI on the distribution of daily city attendance rates.

    Split at the midpoint rather than at a fixed 28 days: the demo profile is
    only 14 days long, and a drift check that silently does nothing on short
    windows is worse than one that adapts and says so.
    """
    frame = query(
        con,
        """
        select local_date, city,
               sum(attendance_expected_met) * 1.0
                 / nullif(sum(attendance_expected_days), 0) as rate
        from main_marts.fct_attendance_daily
        where attendance_expected_days > 0
        group by 1, 2
        having sum(attendance_expected_days) >= 20
        """,
    ).dropna()
    if frame.empty:
        return CheckResult(
            name="attendance_rate_drift",
            category=Category.DRIFT,
            status=Status.WARN,
            explanation="Not enough city-days with attendance to measure drift.",
        )

    days = sorted(frame["local_date"].unique())
    window = min(cfg.dq.psi_trailing_days, len(days) // 2)
    if window < 3:
        return CheckResult(
            name="attendance_rate_drift",
            category=Category.DRIFT,
            status=Status.WARN,
            explanation=(
                f"Only {len(days)} days available; a trailing baseline needs at least 6. "
                "Drift is not being monitored on this profile."
            ),
            details={"days_available": len(days)},
        )
    recent_days = set(days[-window:])
    baseline = frame[~frame["local_date"].isin(recent_days)]["rate"].to_numpy()
    recent = frame[frame["local_date"].isin(recent_days)]["rate"].to_numpy()

    psi = _psi(baseline, recent)
    status = grade(psi, warn=cfg.dq.psi_warn, fail=cfg.dq.psi_fail)
    buckets_used = min(10, max(min(len(baseline), len(recent)) // 8, 2))
    return CheckResult(
        name="attendance_rate_drift",
        category=Category.DRIFT,
        status=status,
        explanation=(
            f"PSI {psi:.3f} between the last {window} days and the {len(days) - window} before "
            f"them (warn {cfg.dq.psi_warn}, fail {cfg.dq.psi_fail})."
        ),
        value=psi,
        threshold=cfg.dq.psi_fail,
        details={
            "baseline_days": len(days) - window,
            "recent_days": window,
            "baseline_observations": int(len(baseline)),
            "buckets": buckets_used,
        },
    )


# --- 6. Reconciliation -----------------------------------------------------


@check(
    "mart_vs_staging_reconciliation",
    Category.RECONCILIATION,
    "Three metrics computed from the marts equal the same metrics computed from staging.",
)
def mart_vs_staging_reconciliation(con, cfg: Config) -> list[CheckResult]:
    """Recompute from the other end of the pipeline and compare.

    This is the check that catches the errors the others cannot see: a join
    that drops rows, a filter applied in one layer and not another, a
    de-duplication that removed more than it should. Every other check asks
    whether a table is internally consistent; this one asks whether the
    warehouse still agrees with the source it was built from.
    """
    tolerance = cfg.dq.reconciliation_tolerance
    comparisons = [
        (
            "attendance_count",
            "select count(distinct emp_id || '|' || business_date) as v "
            "from main_staging.stg_badge_taps",
            "select sum(attendance_daily) as v from main_marts.fct_attendance_daily",
            "people-days with badge activity",
        ),
        (
            "headcount_days",
            "select count(*) as v from main_staging.stg_hr_employee_snapshot "
            "where not is_post_exit and not is_pre_hire",
            "select sum(headcount_incumbent) as v from main_marts.fct_attendance_daily",
            "employed employee-days",
        ),
        (
            "delivered_workstations",
            "select sum(delivered_workstations) as v from main_staging.stg_workplace_space",
            "select sum(delivered_workstations) as v from main_marts.dim_workplace",
            "delivered desks",
        ),
    ]
    results = []
    for name, staging_sql, mart_sql, label in comparisons:
        staging_value = float(query(con, staging_sql)["v"].iloc[0] or 0)
        mart_value = float(query(con, mart_sql)["v"].iloc[0] or 0)
        denominator = max(abs(staging_value), 1.0)
        drift = abs(mart_value - staging_value) / denominator
        status = grade(drift, warn=tolerance / 2, fail=tolerance)
        results.append(
            CheckResult(
                name=f"reconcile_{name}",
                category=Category.RECONCILIATION,
                status=status,
                explanation=(
                    f"{label}: staging {staging_value:,.0f} vs marts {mart_value:,.0f} "
                    f"({drift:.3%} apart, tolerance {tolerance:.1%})."
                ),
                value=drift,
                threshold=tolerance,
                details={"staging": staging_value, "marts": mart_value},
            )
        )
    return results


# --- 7. Volume anomaly -----------------------------------------------------


@check(
    "volume_anomaly",
    Category.VOLUME,
    "Daily row counts sit within a seasonal baseline for each source.",
)
def volume_anomaly(con, cfg: Config) -> list[CheckResult]:
    """Weekday-aware volume monitoring.

    A flat mean flags every Saturday, so the baseline is per weekday: Saturday
    is compared with other Saturdays. With a fortnight of data that leaves two
    observations per weekday, which is too few for a z-score - so the check
    reports insufficient history rather than inventing confidence it does not
    have.
    """
    sources = {
        "badge_taps": "select business_date as d, count(*) as rows "
        "from main_staging.stg_badge_taps group by 1",
        "hr_employee_snapshot": "select snapshot_date as d, count(*) as rows "
        "from main_staging.stg_hr_employee_snapshot group by 1",
    }
    results = []
    for source, sql in sources.items():
        frame = query(con, sql)
        if frame.empty:
            results.append(
                CheckResult(
                    name=f"volume_{source}",
                    category=Category.VOLUME,
                    status=Status.FAIL,
                    explanation=f"{source} produced no rows at all.",
                )
            )
            continue
        frame["d"] = pd.to_datetime(frame["d"])
        frame["weekday"] = frame["d"].dt.weekday
        latest = frame.loc[frame["d"].idxmax()]
        peers = frame[(frame["weekday"] == latest["weekday"]) & (frame["d"] < latest["d"])]["rows"]

        if len(peers) < 3:
            results.append(
                CheckResult(
                    name=f"volume_{source}",
                    category=Category.VOLUME,
                    status=Status.WARN,
                    explanation=(
                        f"{source}: only {len(peers)} prior {latest['d'].strftime('%A')}s in the "
                        "window - not enough history for a seasonal baseline."
                    ),
                    value=float(latest["rows"]),
                    details={"peers": int(len(peers))},
                )
            )
            continue

        mean, std = float(peers.mean()), float(peers.std(ddof=1) or 1.0)
        z = abs(float(latest["rows"]) - mean) / max(std, 1.0)
        status = grade(z, warn=cfg.dq.volume_anomaly_z_warn, fail=cfg.dq.volume_anomaly_z_fail)
        results.append(
            CheckResult(
                name=f"volume_{source}",
                category=Category.VOLUME,
                status=status,
                explanation=(
                    f"{source}: {int(latest['rows']):,} rows on the latest "
                    f"{latest['d'].strftime('%A')}, {z:.1f} standard deviations from the "
                    f"{len(peers)}-week baseline of {mean:,.0f}."
                ),
                value=z,
                threshold=cfg.dq.volume_anomaly_z_fail,
            )
        )
    return results
