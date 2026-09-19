"""Source system 5: ``workplace_dim`` - the space extract.

Grain is (workplace_code, tower, floor): one row per floor, as the brief
describes it and as real space extracts arrive. The warehouse rolls it up to a
workplace-grain dimension in staging rather than pretending the source is
already at the grain the marts want.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from ..config import Config
from .rng import lognormal_from_mean, substream


def generate_workplaces(cfg: Config) -> pd.DataFrame:
    rng = substream(cfg.seed, "workplaces")
    cities = cfg.cities
    n_workplaces = cfg.profile.n_workplaces

    # Distribute workplaces over cities: every city gets one, the remainder is
    # allocated by a Zipf-ish weight so a few cities behave like major hubs.
    weights = 1.0 / np.arange(1, len(cities) + 1) ** 0.7
    weights = weights / weights.sum()
    extra = max(n_workplaces - len(cities), 0)
    extra_counts = rng.multinomial(extra, weights)
    per_city = {c.name: 1 + int(extra_counts[i]) for i, c in enumerate(cities)}

    ws_cfg = cfg.geography.workstations_per_workplace
    # Mean supply per workplace, derived from headcount so the estate is
    # plausibly sized at any profile.
    ws_mean = cfg.profile.n_employees * cfg.geography.workstations_per_employee / n_workplaces
    sqm_cfg = cfg.geography.sqm_per_workstation
    tower_cfg = cfg.geography.towers_per_workplace
    floor_cfg = cfg.geography.floors_per_tower

    rows: list[dict] = []
    seq = 0
    for city in cities:
        city_scale = 1.0 + 0.6 * rng.random()
        for _ in range(per_city[city.name]):
            seq += 1
            code = f"WP{seq:04d}"
            delivered_total = int(
                max(
                    ws_cfg["min"],
                    lognormal_from_mean(rng, ws_mean * city_scale, ws_cfg["sigma"], 1)[0],
                )
            )
            n_towers = int(rng.integers(tower_cfg["min"], tower_cfg["max"] + 1))
            tower_floors = [
                int(rng.integers(floor_cfg["min"], floor_cfg["max"] + 1)) for _ in range(n_towers)
            ]
            total_floors = sum(tower_floors)

            # Split delivered workstations across floors with a Dirichlet so
            # floors are uneven, then repair rounding against the total.
            shares = rng.dirichlet(np.full(total_floors, 8.0))
            per_floor = np.maximum(1, np.round(shares * delivered_total).astype(int))
            per_floor[0] += delivered_total - int(per_floor.sum())
            per_floor = np.maximum(per_floor, 1)

            lo, hi = cfg.metrics.allocated_share_bounds
            allocated_share = float(
                np.clip(
                    rng.normal(
                        cfg.metrics.allocated_share_of_delivered,
                        cfg.metrics.allocated_share_sigma,
                    ),
                    lo,
                    hi,
                )
            )
            audited = bool(rng.random() < cfg.geography.space_audited_share)
            lease_offset = int(
                rng.integers(
                    cfg.geography.lease_expiry_window_days["min"],
                    cfg.geography.lease_expiry_window_days["max"] + 1,
                )
            )
            lease_expiry = cfg.end_date + timedelta(days=lease_offset)
            cost_cfg = cfg.metrics.cost_per_workstation_month
            cost = float(
                lognormal_from_mean(rng, cost_cfg["mean"] * city_scale, cost_cfg["sigma"], 1)[0]
            )

            idx = 0
            for tower_i, n_floors in enumerate(tower_floors, start=1):
                tower = f"T{tower_i}"
                for floor_i in range(1, n_floors + 1):
                    delivered = int(per_floor[idx])
                    idx += 1
                    sqm_per_ws = float(
                        lognormal_from_mean(rng, sqm_cfg["mean"], sqm_cfg["sigma"], 1)[0]
                    )
                    allocated = int(round(delivered * allocated_share))
                    free_sharing = int(round(delivered * cfg.metrics.free_sharing_share))
                    # Regional source difference, deliberately preserved.
                    #
                    # The CN space system reports the free-sharing pool INSIDE
                    # allocated_workstations; everywhere else the two columns
                    # are disjoint. Same column name, different meaning. Adding
                    # them everywhere double-counts CN capacity by ~8%, and
                    # since the column names match, nothing errors - the number
                    # is just wrong. Reconciling this behind one metric name is
                    # the reason the semantic layer exists.
                    if city.region == "CN":
                        allocated = allocated + free_sharing
                    rows.append(
                        {
                            "workplace_code": code,
                            "name": f"{city.name} Office {seq}",
                            "city": city.name,
                            "country": city.country,
                            "region": city.region,
                            "timezone": city.tz,
                            "tower": tower,
                            "floor": floor_i,
                            "delivered_workstations": delivered,
                            "allocated_workstations": allocated,
                            "free_sharing_workstations": free_sharing,
                            "net_workstation_area_sqm": round(delivered * sqm_per_ws, 2),
                            "cost_per_workstation_month": round(cost, 2),
                            "lease_expiry_date": lease_expiry,
                            "is_space_audited": audited,
                        }
                    )

    df = pd.DataFrame(rows)
    df = _inject_defects(cfg, df)
    return df


def _inject_defects(cfg: Config, df: pd.DataFrame) -> pd.DataFrame:
    """Null out the timezone on a handful of rows.

    A missing timezone is the single most damaging defect in this domain: it
    silently shifts an entire workplace's attendance to the wrong local date.
    """
    n = cfg.defects.workplace_null_timezone
    if n <= 0:
        return df
    rng = substream(cfg.seed, "workplaces_defects")
    victims = rng.choice(df["workplace_code"].unique(), size=min(n, df["workplace_code"].nunique()))
    df.loc[df["workplace_code"].isin(victims), "timezone"] = None
    return df
