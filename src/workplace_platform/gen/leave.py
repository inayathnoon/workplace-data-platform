"""Source system 3: ``leave_requests``.

Leave is request-grain, not day-grain, and requests overlap, get cancelled, and
arrive after the fact. Flattening this to "was the employee on leave on day D"
is one of the two genuinely fiddly parts of the central fact table.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from ..config import Config
from .rng import choice_from_mix, lognormal_from_mean, substream


def generate_leave(cfg: Config, master: pd.DataFrame) -> pd.DataFrame:
    rng = substream(cfg.seed, "leave")
    lv = cfg.planted.leave
    n_emp = len(master)
    n_days = cfg.profile.n_days

    # Number of spells per employee over the window.
    spells = rng.binomial(n_days, lv["daily_hazard"], size=n_emp)
    total = int(spells.sum())
    if total == 0:
        return pd.DataFrame(
            columns=[
                "leave_id", "emp_id", "leave_type", "start_date", "end_date",
                "status", "applied_ts",
            ]
        )

    emp_ids = np.repeat(master["emp_id"].to_numpy(), spells)
    start_offsets = rng.integers(0, n_days, size=total)
    durations = np.clip(
        np.round(
            lognormal_from_mean(rng, lv["duration_days"]["mean"], lv["duration_days"]["sigma"], total)
        ),
        1,
        lv["duration_days"]["max"],
    ).astype(int)

    start_dates = np.array([cfg.start_date + timedelta(days=int(o)) for o in start_offsets])
    end_dates = np.array(
        [s + timedelta(days=int(d) - 1) for s, d in zip(start_dates, durations, strict=True)]
    )

    # Application timing: most requests precede the leave, a planted share are
    # applied retroactively (sickness, mostly).
    retro = rng.random(total) < lv["retro_applied_share"]
    lead_days = rng.integers(1, 28, size=total)
    applied = []
    for s, e, is_retro, lead in zip(start_dates, end_dates, retro, lead_days, strict=True):
        if is_retro:
            applied_day = min(e + timedelta(days=int(rng.integers(0, 3))), cfg.end_date)
        else:
            applied_day = s - timedelta(days=int(lead))
        applied.append(
            datetime.combine(applied_day, time(int(rng.integers(7, 20)), int(rng.integers(0, 60))))
        )

    df = pd.DataFrame(
        {
            "leave_id": [f"LV{i:08d}" for i in range(total)],
            "emp_id": emp_ids,
            "leave_type": choice_from_mix(rng, lv["type_mix"], total),
            "start_date": start_dates,
            "end_date": end_dates,
            "status": choice_from_mix(rng, lv["status_mix"], total),
            "applied_ts": applied,
        }
    )

    df = _inject_overlaps(cfg, df, rng)
    return df.sort_values(["emp_id", "start_date"]).reset_index(drop=True)


def _inject_overlaps(cfg: Config, df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Duplicate a share of requests with shifted, overlapping dates.

    Real leave systems contain these: an employee extends a booking by raising a
    second request rather than amending the first. Naive day-expansion then
    double-counts the overlap.
    """
    n_extra = int(round(len(df) * cfg.planted.leave["overlapping_share"]))
    if n_extra <= 0:
        return df
    picked = df.sample(n=min(n_extra, len(df)), random_state=int(rng.integers(1 << 31)))
    extra = picked.copy()
    shift = rng.integers(-2, 3, size=len(extra))
    extra["leave_id"] = [f"LVX{i:07d}" for i in range(len(extra))]
    extra["start_date"] = [
        s + timedelta(days=int(k)) for s, k in zip(extra["start_date"], shift, strict=True)
    ]
    extra["end_date"] = [
        e + timedelta(days=int(k) + 1) for e, k in zip(extra["end_date"], shift, strict=True)
    ]
    return pd.concat([df, extra], ignore_index=True)


def leave_days(df: pd.DataFrame) -> set[tuple[str, object]]:
    """Expand approved leave to a set of (emp_id, date) - the simulator's truth.

    Only approved leave counts. Pending and cancelled requests exist precisely
    so that a pipeline which forgets to filter on status gets caught.
    """
    out: set[tuple[str, object]] = set()
    approved = df[df["status"] == "approved"]
    for emp, s, e in zip(approved["emp_id"], approved["start_date"], approved["end_date"], strict=True):
        day = s
        while day <= e:
            out.add((emp, day))
            day += timedelta(days=1)
    return out
