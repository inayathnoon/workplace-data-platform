"""Source system 4: ``travel_bookings``.

A trip removes an employee from their home workplace without being absence.
Conflating the two is the classic way to overstate no-shows.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from ..config import Config
from .rng import substream


def generate_travel(cfg: Config, master: pd.DataFrame) -> pd.DataFrame:
    rng = substream(cfg.seed, "travel")
    tv = cfg.planted.travel
    n_emp = len(master)
    n_days = cfg.profile.n_days

    trips = rng.binomial(n_days, tv["trip_hazard"], size=n_emp)
    total = int(trips.sum())
    columns = [
        "booking_id", "emp_id", "origin_city", "destination_city", "depart_date",
        "return_date", "booking_status", "hotel_flag", "flight_flag",
    ]
    if total == 0:
        return pd.DataFrame(columns=columns)

    emp_ids = np.repeat(master["emp_id"].to_numpy(), trips)
    origins = np.repeat(master["base_city"].to_numpy(), trips)

    city_names = [c.name for c in cfg.cities]
    destinations = np.array(
        [
            rng.choice([c for c in city_names if c != o]) if len(city_names) > 1 else o
            for o in origins
        ]
    )

    depart_offsets = rng.integers(0, n_days, size=total)
    durations = rng.integers(tv["duration_days"]["min"], tv["duration_days"]["max"] + 1, size=total)
    depart = np.array([cfg.start_date + timedelta(days=int(o)) for o in depart_offsets])
    ret = np.array(
        [d + timedelta(days=int(k)) for d, k in zip(depart, durations, strict=True)]
    )

    status = np.where(rng.random(total) < tv["cancelled_share"], "cancelled", "confirmed")

    return pd.DataFrame(
        {
            "booking_id": [f"TR{i:08d}" for i in range(total)],
            "emp_id": emp_ids,
            "origin_city": origins,
            "destination_city": destinations,
            "depart_date": depart,
            "return_date": ret,
            "booking_status": status,
            "hotel_flag": rng.random(total) < tv["hotel_share"],
            "flight_flag": rng.random(total) < tv["flight_share"],
        }
    ).sort_values(["emp_id", "depart_date"]).reset_index(drop=True)


def travel_days(df: pd.DataFrame) -> dict[tuple[str, object], str]:
    """Map (emp_id, date) -> destination city for confirmed trips."""
    out: dict[tuple[str, object], str] = {}
    if df.empty:
        return out
    confirmed = df[df["booking_status"] == "confirmed"]
    for emp, dep, ret, dest in zip(
        confirmed["emp_id"],
        confirmed["depart_date"],
        confirmed["return_date"],
        confirmed["destination_city"],
        strict=True,
    ):
        day = dep
        while day <= ret:
            out[(emp, day)] = dest
            day += timedelta(days=1)
    return out
