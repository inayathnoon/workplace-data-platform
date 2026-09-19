"""Generate all five source systems and write them to ``data/raw/``.

Nothing else in the repository writes to ``data/raw/``. Everything downstream
reads it as if it were five unrelated production systems that happen to land in
the same object store - because that is what it is standing in for.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ..config import DEFECT_MANIFEST, GROUND_TRUTH, RAW_DIR, Config, load_config
from .employees import build_defect_plan, generate_employees, snapshot_for_date
from .leave import generate_leave, leave_days
from .rng import substream
from .taps import TapGenerator
from .travel import generate_travel, travel_days
from .workplaces import generate_workplaces

# Columns the simulator knows but no source system would ever publish.
_TRUTH_ONLY_TAP_COLUMNS = ["local_hour", "is_post_termination"]


def _date_range(cfg: Config) -> list[date]:
    return [cfg.start_date + timedelta(days=i) for i in range(cfg.profile.n_days)]


def _write_partitioned(frames: dict[date, pd.DataFrame], root: Path, name: str) -> int:
    """One Parquet file per day, under ``<root>/<name>/dt=YYYY-MM-DD/``."""
    total = 0
    for day, frame in frames.items():
        if frame is None or frame.empty:
            continue
        out_dir = root / name / f"dt={day.isoformat()}"
        out_dir.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(out_dir / "part-000.parquet", index=False)
        total += len(frame)
    return total


def _write_table(frame: pd.DataFrame, root: Path, name: str) -> int:
    out_dir = root / name
    out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out_dir / "part-000.parquet", index=False)
    return len(frame)


def generate_all(cfg: Config | None = None, clean: bool = True) -> dict:
    cfg = cfg or load_config()
    if clean and RAW_DIR.exists():
        shutil.rmtree(RAW_DIR)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    days = _date_range(cfg)
    row_counts: dict[str, int] = {}

    # 5. Space extract first - everything else is positioned against it.
    workplaces = generate_workplaces(cfg)
    row_counts["workplace_dim"] = _write_table(workplaces, RAW_DIR, "workplace_dim")

    # 1. Employee master (private) and the daily snapshot it implies.
    master = generate_employees(cfg, workplaces)
    plan = build_defect_plan(cfg, master)
    snap_rng = substream(cfg.seed, "hr_snapshot")
    nulled_cities = 0
    snapshots: dict[date, pd.DataFrame] = {}
    for day in days:
        frame, nulled = snapshot_for_date(cfg, master, day, plan, snap_rng)
        snapshots[day] = frame
        nulled_cities += nulled
    row_counts["hr_employee_snapshot"] = _write_partitioned(
        snapshots, RAW_DIR, "hr_employee_snapshot"
    )
    del snapshots

    # 3 and 4. Absence and travel, which gate attendance.
    leave = generate_leave(cfg, master)
    row_counts["leave_requests"] = _write_table(leave, RAW_DIR, "leave_requests")
    travel = generate_travel(cfg, master)
    row_counts["travel_bookings"] = _write_table(travel, RAW_DIR, "travel_bookings")

    leave_by_day: dict[date, set[str]] = defaultdict(set)
    for emp, day in leave_days(leave):
        leave_by_day[day].add(emp)
    travel_by_day: dict[date, dict[str, str]] = defaultdict(dict)
    for (emp, day), dest in travel_days(travel).items():
        travel_by_day[day][emp] = dest

    # 2. Badge taps, day by day so memory stays flat at any profile size.
    gen = TapGenerator(cfg, master, workplaces)
    tap_rows = 0
    for day in days:
        frame = gen.generate_day(day, leave_by_day.get(day, set()), travel_by_day.get(day, {}))
        if frame.empty:
            continue
        published = frame.drop(columns=_TRUTH_ONLY_TAP_COLUMNS)
        out_dir = RAW_DIR / "badge_taps" / f"dt={day.isoformat()}"
        out_dir.mkdir(parents=True, exist_ok=True)
        published.to_parquet(out_dir / "part-000.parquet", index=False)
        tap_rows += len(published)
    row_counts["badge_taps"] = tap_rows

    # --- manifests ---------------------------------------------------------
    defect_counts = {
        **{k: int(v) for k, v in plan["counts"].items()},
        "hr_null_base_city_rows": int(nulled_cities),
        "workplace_null_timezone_workplaces": int(
            workplaces.loc[workplaces["timezone"].isna(), "workplace_code"].nunique()
        ),
        "workplace_null_timezone_rows": int(workplaces["timezone"].isna().sum()),
        **gen.defects.as_dict(),
    }
    DEFECT_MANIFEST.write_text(json.dumps(defect_counts, indent=2, sort_keys=True))

    ground_truth = {
        "profile": cfg.profile_name,
        "seed": cfg.seed,
        "start_date": cfg.start_date.isoformat(),
        "end_date": cfg.end_date.isoformat(),
        "row_counts": row_counts,
        "planted": {
            "attendance_rate_by_region_configured": cfg.planted.attendance_rate_by_region,
            "attendance_rate_by_region_realised": gen.tally.realised_rates(),
            "scheduled_days_per_week": cfg.planted.scheduled_days_per_week,
            "off_schedule_attendance_rate": cfg.planted.off_schedule_attendance_rate,
            "seat_demand_buffer": cfg.metrics.seat_demand_buffer,
        },
        "scheduled_employee_days_by_region": gen.tally.scheduled_eligible,
        "attended_employee_days_by_region": gen.tally.scheduled_attended,
    }
    GROUND_TRUTH.write_text(json.dumps(ground_truth, indent=2, sort_keys=True))

    return {"row_counts": row_counts, "defects": defect_counts, "ground_truth": ground_truth}


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    result = generate_all()
    for table, count in sorted(result["row_counts"].items()):
        print(f"{table:28s} {count:>12,}")
