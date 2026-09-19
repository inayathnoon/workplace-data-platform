"""Source system 1: ``hr_employee_snapshot``.

The HR system publishes a full daily snapshot rather than a change feed, which
is the common and annoying case: you get 120k rows a day, most of them
identical, and you have to derive validity intervals yourself.

This module produces the employee master (never published - it is the
simulator's private truth) and the daily snapshot rows derived from it.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..config import Config
from .rng import beta_around, choice_from_mix, lognormal_from_mean, substream

# How long before their hire date an employee appears in the snapshot, and how
# long after their termination date they linger. Both are properties of the
# source system, not of the employee.
PRE_HIRE_VISIBILITY_DAYS = 14
POST_EXIT_VISIBILITY_DAYS = 30

WEEKDAYS = [0, 1, 2, 3, 4]  # Monday .. Friday


def _build_hierarchy(cfg: Config, rng: np.random.Generator) -> list[dict]:
    """Return one record per L4 team, carrying its full ancestry."""
    wf = cfg.workforce
    teams: list[dict] = []
    for l1 in wf.departments_l1:
        n_l2 = int(rng.integers(wf.l2_per_l1["min"], wf.l2_per_l1["max"] + 1))
        for i2 in range(1, n_l2 + 1):
            l2 = f"{l1} Division {i2}"
            n_l3 = int(rng.integers(wf.l3_per_l2["min"], wf.l3_per_l2["max"] + 1))
            for i3 in range(1, n_l3 + 1):
                l3 = f"{l2} Group {i3}"
                n_l4 = int(rng.integers(wf.l4_per_l3["min"], wf.l4_per_l3["max"] + 1))
                for i4 in range(1, n_l4 + 1):
                    teams.append(
                        {
                            "dept_l1": l1,
                            "dept_l2": l2,
                            "dept_l3": l3,
                            "dept_l4": f"{l3} Team {i4}",
                        }
                    )
    return teams


def _scheduled_weekdays(cfg: Config, rng: np.random.Generator, n_days: int) -> tuple[int, ...]:
    """Pick which weekdays a team is expected in, honouring the Tue-Thu skew."""
    weights = np.array(cfg.planted.weekday_weights, dtype=float)
    weights = weights / weights.sum()
    chosen = rng.choice(WEEKDAYS, size=n_days, replace=False, p=weights)
    return tuple(sorted(int(d) for d in chosen))


def generate_employees(cfg: Config, workplaces: pd.DataFrame) -> pd.DataFrame:
    """The employee master: one row per employee, the simulator's ground truth."""
    rng = substream(cfg.seed, "employees")
    n = cfg.profile.n_employees
    wf = cfg.workforce

    teams = _build_hierarchy(cfg, rng)
    team_sizes = np.maximum(
        wf.team_size["min"],
        lognormal_from_mean(rng, wf.team_size["mean"], wf.team_size["sigma"], len(teams)),
    )
    team_p = team_sizes / team_sizes.sum()
    team_idx = rng.choice(len(teams), size=n, p=team_p)

    # Each L2 division fixes its own office-day pattern: the number of days is
    # planted per L1, the specific weekdays vary by division.
    l2_schedule: dict[str, tuple[int, ...]] = {}
    for t in teams:
        if t["dept_l2"] not in l2_schedule:
            n_days = cfg.planted.scheduled_days_per_week[t["dept_l1"]]
            l2_schedule[t["dept_l2"]] = _scheduled_weekdays(cfg, rng, n_days)

    # Workplace assignment: weight cities by delivered capacity, then place the
    # employee in that city's largest workplace unless they are a cross-worker.
    ws_by_workplace = (
        workplaces.groupby(["workplace_code", "city", "region"], as_index=False)[
            "delivered_workstations"
        ]
        .sum()
        .sort_values("delivered_workstations", ascending=False)
    )
    codes = ws_by_workplace["workplace_code"].to_numpy()
    code_city = dict(zip(codes, ws_by_workplace["city"], strict=True))
    code_region = dict(zip(codes, ws_by_workplace["region"], strict=True))
    capacity_p = ws_by_workplace["delivered_workstations"].to_numpy(dtype=float)
    capacity_p = capacity_p / capacity_p.sum()

    home_workplace = rng.choice(codes, size=n, p=capacity_p)
    base_city = np.array([code_city[c] for c in home_workplace])

    # Cross-workplace employees sit somewhere other than their base-city hub.
    theory_workplace = home_workplace.copy()
    cross = rng.random(n) < wf.cross_workplace_share
    by_city: dict[str, list[str]] = {}
    for c in codes:
        by_city.setdefault(code_city[c], []).append(c)
    for i in np.flatnonzero(cross):
        options = [c for c in by_city[base_city[i]] if c != home_workplace[i]]
        if options:
            theory_workplace[i] = options[int(rng.integers(len(options)))]

    region = np.array([code_region[c] for c in theory_workplace])

    # Dates. Tenure is lognormal, clipped so nobody predates the company.
    tenure = np.minimum(
        lognormal_from_mean(rng, wf.tenure_days["mean"], wf.tenure_days["sigma"], n),
        wf.tenure_days["max"],
    )
    hire_date = np.array(
        [cfg.end_date - timedelta(days=int(t)) for t in tenure], dtype="object"
    )

    status = choice_from_mix(rng, wf.employment_status_mix, n)

    # Onboarding employees are hired inside (or just after) the window, so the
    # pipeline actually sees the transition rather than a static label.
    onboarding = status == "onboarding"
    if onboarding.any():
        offsets = rng.integers(-cfg.profile.n_days, 11, size=int(onboarding.sum()))
        hire_date[onboarding] = [cfg.end_date - timedelta(days=int(o)) for o in offsets]

    term_date = np.full(n, None, dtype="object")
    leaving = np.isin(status, ["resigned", "terminated"])
    leaver_idx = np.flatnonzero(leaving)
    # 60% of leavers depart inside the window (visible transition); the rest
    # left beforehand and should never appear at all.
    inside = rng.random(len(leaver_idx)) < 0.60
    for i, is_inside in zip(leaver_idx, inside, strict=True):
        if is_inside:
            offset = int(rng.integers(0, cfg.profile.n_days))
            term_date[i] = cfg.end_date - timedelta(days=offset)
        else:
            offset = int(rng.integers(1, 400))
            term_date[i] = cfg.start_date - timedelta(days=offset)

    # Assignments: a temporary posting to another city.
    assignment_city = np.full(n, None, dtype="object")
    assignment_start = np.full(n, None, dtype="object")
    assignment_end = np.full(n, None, dtype="object")
    city_names = [c.name for c in cfg.cities]
    for label, starts_in_window in (("on_assignment", True), ("pre_assignment", False)):
        idx = np.flatnonzero(status == label)
        for i in idx:
            other = [c for c in city_names if c != base_city[i]]
            if not other:
                continue
            assignment_city[i] = other[int(rng.integers(len(other)))]
            if starts_in_window:
                start_off = int(rng.integers(0, max(cfg.profile.n_days // 2, 1)))
                start = cfg.start_date + timedelta(days=start_off)
            else:
                start = cfg.end_date + timedelta(days=int(rng.integers(1, 60)))
            assignment_start[i] = start
            assignment_end[i] = start + timedelta(days=int(rng.integers(20, 180)))

    propensity = np.array(
        [
            beta_around(
                rng,
                cfg.planted.attendance_rate_by_region[r],
                cfg.planted.propensity_concentration,
                1,
            )[0]
            for r in region
        ]
    )

    emp_id = np.array([f"E{100000 + i}" for i in range(n)])
    team_df = pd.DataFrame([teams[i] for i in team_idx])

    master = pd.DataFrame(
        {
            "emp_id": emp_id,
            "employee_type": choice_from_mix(rng, wf.employee_type_mix, n),
            "badge_type": choice_from_mix(rng, wf.badge_type_mix, n),
            "dept_l1": team_df["dept_l1"].to_numpy(),
            "dept_l2": team_df["dept_l2"].to_numpy(),
            "dept_l3": team_df["dept_l3"].to_numpy(),
            "dept_l4": team_df["dept_l4"].to_numpy(),
            "base_city": base_city,
            "theory_workplace": theory_workplace,
            "region": region,
            "hire_date": hire_date,
            "term_date": term_date,
            "final_status": status,
            "assignment_city": assignment_city,
            "assignment_start": assignment_start,
            "assignment_end": assignment_end,
            "attendance_propensity": propensity,
            "is_overnight_worker": rng.random(n) < cfg.taps.overnight_worker_share,
            "arrival_offset_hours": rng.normal(0.0, 0.6, n),
        }
    )
    master["scheduled_weekdays"] = [l2_schedule[d] for d in master["dept_l2"]]

    # Managers: the first employee of each L4 team manages it; team managers
    # report upward within their L2. Nulls at the top are intentional.
    master["manager_id"] = _assign_managers(master, rng)
    return master


def _assign_managers(master: pd.DataFrame, rng: np.random.Generator) -> np.ndarray:
    manager_of_team = master.groupby("dept_l4")["emp_id"].first().to_dict()
    manager_ids = master["dept_l4"].map(manager_of_team).to_numpy()

    is_manager = master["emp_id"].to_numpy() == manager_ids
    l2_managers: dict[str, list[str]] = {}
    for l2, emp in zip(master.loc[is_manager, "dept_l2"], master.loc[is_manager, "emp_id"], strict=True):
        l2_managers.setdefault(l2, []).append(emp)

    out = manager_ids.copy()
    for i in np.flatnonzero(is_manager):
        peers = [m for m in l2_managers[master["dept_l2"].iloc[i]] if m != master["emp_id"].iloc[i]]
        out[i] = peers[int(rng.integers(len(peers)))] if peers else None
    return out


# --- Daily snapshot --------------------------------------------------------


def status_on(row_hire: date, row_term, final_status: str, assign_start, assign_end, day: date) -> str:
    """Employment status as HR would report it on ``day``.

    Kept as a single readable function because it is the definition the
    warehouse's precedence table has to agree with, and disagreement between
    the two is exactly the bug class this repo is about.
    """
    if row_term is not None and day > row_term:
        return final_status if final_status in ("resigned", "terminated") else "terminated"
    if day < row_hire:
        return "onboarding"
    if assign_start is not None:
        if day < assign_start:
            return "pre_assignment"
        if assign_end is not None and assign_start <= day <= assign_end:
            return "on_assignment"
    return "active"


def build_defect_plan(cfg: Config, master: pd.DataFrame) -> dict:
    """Decide up front which rows get corrupted, and record the exact counts.

    Injecting defects deterministically and counting them here is what lets the
    DQ suite be graded on recall instead of being trusted.
    """
    rng = substream(cfg.seed, "hr_defects")
    plan: dict = {
        "resigned_still_active": {},   # emp_id -> last date to keep showing active
        "rows_before_hire_date": set(),  # (emp_id, day)
        "null_base_city_rate": cfg.defects.hr_null_base_city_share,
        "counts": {},
    }

    # 1. Resigned employees who keep showing as active for a few days.
    resigned = master[
        (master["final_status"] == "resigned")
        & master["term_date"].notna()
        & master["term_date"].apply(lambda d: d is not None and d >= cfg.start_date)
    ]
    n_victims = int(round(len(resigned) * cfg.defects.hr_resigned_still_active_share))
    n_victims = max(n_victims, 1 if len(resigned) else 0)
    lag_cfg = cfg.defects.hr_resigned_still_active_days
    affected_rows = 0
    if n_victims:
        victims = rng.choice(resigned["emp_id"].to_numpy(), size=n_victims, replace=False)
        term_by_emp = dict(zip(resigned["emp_id"], resigned["term_date"], strict=True))
        for emp in victims:
            lag = int(rng.integers(lag_cfg["min"], lag_cfg["max"] + 1))
            last_bad_day = term_by_emp[emp] + timedelta(days=lag)
            plan["resigned_still_active"][emp] = last_bad_day
            for k in range(1, lag + 1):
                d = term_by_emp[emp] + timedelta(days=k)
                if cfg.start_date <= d <= cfg.end_date:
                    affected_rows += 1
    plan["counts"]["hr_resigned_still_active_employees"] = len(plan["resigned_still_active"])
    plan["counts"]["hr_resigned_still_active_rows"] = affected_rows

    # 2. Rows that appear before the employee's own hire date, labelled active.
    n_early = cfg.defects.hr_rows_before_hire_date
    candidates = master[
        master["hire_date"].apply(lambda d: d > cfg.start_date + timedelta(days=1))
    ]
    if len(candidates) and n_early:
        picked = rng.choice(
            candidates["emp_id"].to_numpy(), size=min(n_early, len(candidates)), replace=False
        )
        hire_by_emp = dict(zip(candidates["emp_id"], candidates["hire_date"], strict=True))
        for emp in picked:
            latest = min(hire_by_emp[emp] - timedelta(days=1), cfg.end_date)
            day = max(latest, cfg.start_date)
            plan["rows_before_hire_date"].add((emp, day))
    plan["counts"]["hr_rows_before_hire_date"] = len(plan["rows_before_hire_date"])
    return plan


def snapshot_for_date(
    cfg: Config, master: pd.DataFrame, day: date, plan: dict, rng: np.random.Generator
) -> tuple[pd.DataFrame, int]:
    """One day of ``hr_employee_snapshot``, plus the count of nulled base cities."""
    hire = master["hire_date"].to_numpy()
    term = master["term_date"].to_numpy()

    visible = np.array(
        [
            (day >= h - timedelta(days=PRE_HIRE_VISIBILITY_DAYS))
            and (t is None or day <= t + timedelta(days=POST_EXIT_VISIBILITY_DAYS))
            for h, t in zip(hire, term, strict=True)
        ]
    )
    early_today = {emp for emp, d in plan["rows_before_hire_date"] if d == day}
    if early_today:
        visible |= master["emp_id"].isin(early_today).to_numpy()

    df = master.loc[visible].copy()
    if df.empty:
        return df, 0

    statuses = [
        status_on(h, t, fs, a_s, a_e, day)
        for h, t, fs, a_s, a_e in zip(
            df["hire_date"],
            df["term_date"],
            df["final_status"],
            df["assignment_start"],
            df["assignment_end"],
            strict=True,
        )
    ]
    df["employment_status"] = statuses

    # Defect 1: resigned employees still reported active.
    still_active = plan["resigned_still_active"]
    if still_active:
        mask = df["emp_id"].map(lambda e: e in still_active and day <= still_active[e])
        df.loc[mask.to_numpy() & (df["employment_status"] != "active").to_numpy(), "employment_status"] = "active"

    # Defect 2: rows before the hire date, reported active.
    if early_today:
        df.loc[df["emp_id"].isin(early_today), "employment_status"] = "active"

    # Defect 3: a trickle of null base cities.
    null_mask = rng.random(len(df)) < plan["null_base_city_rate"]
    df["base_city_out"] = df["base_city"].where(~null_mask, None)

    out = pd.DataFrame(
        {
            "snapshot_date": day,
            "emp_id": df["emp_id"].to_numpy(),
            "employment_status": df["employment_status"].to_numpy(),
            "hire_date": df["hire_date"].to_numpy(),
            "term_date": df["term_date"].to_numpy(),
            "badge_type": df["badge_type"].to_numpy(),
            "employee_type": df["employee_type"].to_numpy(),
            "dept_l1": df["dept_l1"].to_numpy(),
            "dept_l2": df["dept_l2"].to_numpy(),
            "dept_l3": df["dept_l3"].to_numpy(),
            "dept_l4": df["dept_l4"].to_numpy(),
            "base_city": df["base_city_out"].to_numpy(),
            "theory_workplace": df["theory_workplace"].to_numpy(),
            "manager_id": df["manager_id"].to_numpy(),
            "assignment_city": df["assignment_city"].to_numpy(),
        }
    )
    return out, int(null_mask.sum())
