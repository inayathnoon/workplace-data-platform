"""Pandera schemas for every dataframe that crosses a module boundary.

These are contracts, not data quality checks, and the distinction matters:

* A contract failure means the code is wrong - a column was renamed, a type
  drifted, a grain was broken. It should stop the pipeline immediately.
* A DQ failure (``src/dq/``) means the *data* is wrong in a way the business
  needs to know about. It is reported, scored, and sometimes tolerated.

So the schemas below deliberately do not encode business rules. A tap from a
terminated employee satisfies this contract; it is the DQ layer's job to say
that it should not exist.
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema

EMPLOYMENT_STATUSES = [
    "active",
    "onboarding",
    "resigned",
    "terminated",
    "on_assignment",
    "pre_assignment",
]
EMPLOYEE_TYPES = ["FTE", "intern", "contractor", "BPO"]
LEAVE_TYPES = ["annual", "sick", "parental", "unpaid", "comp"]
LEAVE_STATUSES = ["approved", "pending", "cancelled"]
REGIONS = ["AMER", "EMEA", "APAC", "CN"]

HR_SNAPSHOT = DataFrameSchema(
    {
        "snapshot_date": Column(object, nullable=False),
        "emp_id": Column(str, nullable=False),
        "employment_status": Column(str, pa.Check.isin(EMPLOYMENT_STATUSES), nullable=False),
        "hire_date": Column(object, nullable=False),
        "term_date": Column(object, nullable=True),
        "badge_type": Column(str, nullable=False),
        "employee_type": Column(str, pa.Check.isin(EMPLOYEE_TYPES), nullable=False),
        "dept_l1": Column(str, nullable=False),
        "dept_l2": Column(str, nullable=False),
        "dept_l3": Column(str, nullable=False),
        "dept_l4": Column(str, nullable=False),
        # Nullable on purpose: a planted defect nulls a fraction of these, and a
        # contract that rejected them would hide the thing the repo is showing.
        "base_city": Column(str, nullable=True),
        "theory_workplace": Column(str, nullable=False),
        "manager_id": Column(str, nullable=True),
        "assignment_city": Column(str, nullable=True),
    },
    # (snapshot_date, emp_id) is the grain, and it is the one thing about this
    # source we refuse to tolerate being wrong.
    unique=["snapshot_date", "emp_id"],
    strict=True,
    coerce=True,
)

BADGE_TAPS = DataFrameSchema(
    {
        "emp_id": Column(str, nullable=False),
        "workplace_code": Column(str, nullable=False),
        "tower": Column(str, nullable=False),
        "floor": Column(int, pa.Check.gt(0), nullable=False),
        "tap_ts_utc": Column("datetime64[ns]", nullable=False),
        "direction": Column(str, pa.Check.isin(["in", "out"]), nullable=False),
        "device_id": Column(str, nullable=False),
        "ingested_date": Column(object, nullable=False),
    },
    # No uniqueness constraint: duplicate taps are expected in the raw feed.
    strict=True,
    coerce=True,
)

LEAVE_REQUESTS = DataFrameSchema(
    {
        "leave_id": Column(str, nullable=False, unique=True),
        "emp_id": Column(str, nullable=False),
        "leave_type": Column(str, pa.Check.isin(LEAVE_TYPES), nullable=False),
        "start_date": Column(object, nullable=False),
        "end_date": Column(object, nullable=False),
        "status": Column(str, pa.Check.isin(LEAVE_STATUSES), nullable=False),
        "applied_ts": Column("datetime64[ns]", nullable=False),
    },
    strict=True,
    coerce=True,
)

TRAVEL_BOOKINGS = DataFrameSchema(
    {
        "booking_id": Column(str, nullable=False, unique=True),
        "emp_id": Column(str, nullable=False),
        "origin_city": Column(str, nullable=False),
        "destination_city": Column(str, nullable=False),
        "depart_date": Column(object, nullable=False),
        "return_date": Column(object, nullable=False),
        "booking_status": Column(str, pa.Check.isin(["confirmed", "cancelled"]), nullable=False),
        "hotel_flag": Column(bool, nullable=False),
        "flight_flag": Column(bool, nullable=False),
    },
    strict=True,
    coerce=True,
)

WORKPLACE_DIM = DataFrameSchema(
    {
        "workplace_code": Column(str, nullable=False),
        "name": Column(str, nullable=False),
        "city": Column(str, nullable=False),
        "country": Column(str, nullable=False),
        "region": Column(str, pa.Check.isin(REGIONS), nullable=False),
        # Nullable: the missing-timezone defect lives here.
        "timezone": Column(str, nullable=True),
        "tower": Column(str, nullable=False),
        "floor": Column(int, pa.Check.gt(0), nullable=False),
        "delivered_workstations": Column(int, pa.Check.ge(0), nullable=False),
        "allocated_workstations": Column(int, pa.Check.ge(0), nullable=False),
        "free_sharing_workstations": Column(int, pa.Check.ge(0), nullable=False),
        "net_workstation_area_sqm": Column(float, pa.Check.ge(0), nullable=False),
        "cost_per_workstation_month": Column(float, pa.Check.gt(0), nullable=False),
        "lease_expiry_date": Column(object, nullable=False),
        "is_space_audited": Column(bool, nullable=False),
    },
    unique=["workplace_code", "tower", "floor"],
    strict=True,
    coerce=True,
)

DEPT_SCHEDULE = DataFrameSchema(
    {
        "dept_l2": Column(str, nullable=False),
        "dept_l1": Column(str, nullable=False),
        "weekday": Column(int, pa.Check.in_range(0, 6), nullable=False),
        "is_scheduled_office_day": Column(bool, nullable=False),
        "required_days_per_week": Column(int, pa.Check.in_range(0, 7), nullable=False),
        "policy_version": Column(str, nullable=False),
    },
    unique=["dept_l2", "weekday"],
    strict=True,
    coerce=True,
)

SOURCE_SCHEMAS: dict[str, DataFrameSchema] = {
    "hr_employee_snapshot": HR_SNAPSHOT,
    "badge_taps": BADGE_TAPS,
    "leave_requests": LEAVE_REQUESTS,
    "travel_bookings": TRAVEL_BOOKINGS,
    "workplace_dim": WORKPLACE_DIM,
    "dept_schedule": DEPT_SCHEDULE,
}
