"""Properties of the built warehouse.

These run against the DuckDB file, so they are skipped rather than failed when
it does not exist - a fresh clone should not see a wall of red before the
pipeline has ever run.
"""

from __future__ import annotations

import json

import pytest

from workplace_platform.config import GROUND_TRUTH
from workplace_platform.semantic.compile import compile_metric


def scalar(con, sql: str):
    return con.execute(sql).fetchone()[0]


def test_employee_day_is_unique(con):
    assert (
        scalar(
            con,
            "select count(*) from (select emp_id, local_date from "
            "main_intermediate.int_employee_day group by 1,2 having count(*) > 1)",
        )
        == 0
    )


def test_every_employee_day_has_exactly_one_state(con):
    total = scalar(con, "select count(*) from main_intermediate.int_employee_day")
    labelled = scalar(
        con,
        "select count(*) from main_intermediate.int_employee_day "
        "where employee_day_state is not null",
    )
    assert total == labelled > 0


def test_precedence_termination_beats_leave(con):
    """A terminated employee with approved leave is terminated, not on leave.
    The cascade says so; this proves the SQL agrees with the cascade."""
    assert (
        scalar(
            con,
            "select count(*) from main_intermediate.int_employee_day "
            "where employee_day_state = 'on_leave' and term_date is not null "
            "and local_date > term_date",
        )
        == 0
    )


def test_duplicate_taps_are_removed(con):
    """The generator injects re-swipes within 90 seconds; staging must collapse
    them, and must do it on a window rather than on equality."""
    raw = scalar(con, "select count(*) from raw.badge_taps")
    staged = scalar(con, "select count(*) from main_staging.stg_badge_taps")
    assert staged < raw


def test_overnight_shift_is_one_business_day(con):
    """An entry at 17:30 and an exit at 01:10 is one day. Without the anchor
    rule the night-shift population is counted twice."""
    crossing = scalar(
        con,
        "select count(*) from main_staging.stg_badge_taps where business_date != local_date",
    )
    assert crossing > 0, "no overnight taps were generated; the rule is untested"
    mismatched = scalar(
        con,
        """
        select count(*) from main_staging.stg_badge_taps
        where direction = 'out' and is_overnight_tap and business_date >= local_date
        """,
    )
    assert mismatched == 0


def test_attendance_never_exceeds_the_population(con):
    assert (
        scalar(
            con,
            "select count(*) from main_marts.fct_attendance_daily "
            "where attendance_expected_met > attendance_expected_days "
            "or attendance_daily > employee_days",
        )
        == 0
    )


def test_capacity_identities_hold_exactly(con):
    assert (
        scalar(
            con,
            """
        select count(*) from main_marts.fct_workplace_capacity_daily
        where workstation_waste != available_workstations - attendance_actual
           or delivered_workstations != allocated_workstations + unallocated_workstations
        """,
        )
        == 0
    )


def test_cn_available_excludes_the_sharing_pool(con):
    """In CN the pool is already inside allocated. Adding it would overstate
    supply by about 8% - with no error, because the column names match."""
    assert (
        scalar(
            con,
            "select count(*) from main_marts.dim_workplace "
            "where region = 'CN' and available_workstations != allocated_workstations",
        )
        == 0
    )
    assert (
        scalar(
            con,
            "select count(*) from main_marts.dim_workplace "
            "where region != 'CN' "
            "and available_workstations != allocated_workstations + free_sharing_workstations",
        )
        == 0
    )


@pytest.mark.parametrize("region", ["AMER", "EMEA", "APAC", "CN"])
def test_attendance_rate_recovers_the_planted_rate(con, cfg, registry, region):
    """The headline claim of the repo, asserted rather than asserted-in-prose."""
    compiled = compile_metric(registry, "attendance_rate", ["region"], None, cfg=cfg)
    frame = con.execute(compiled.sql).df().set_index("region")
    planted = json.loads(GROUND_TRUTH.read_text())["planted"]["attendance_rate_by_region_realised"]
    recovered = float(frame.loc[region, "attendance_rate"])
    assert recovered == pytest.approx(planted[region], abs=0.001)
