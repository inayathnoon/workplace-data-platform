"""Typed access to ``conf/sim.yaml``.

The whole repository is a function of this one file, so it is loaded once,
validated, and passed around explicitly. Nothing reads the YAML directly.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "conf" / "sim.yaml"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Profile(_Base):
    n_employees: int
    n_days: int
    n_cities: int
    n_workplaces: int
    end_date: date


class City(_Base):
    name: str
    country: str
    region: str
    tz: str


class Geography(_Base):
    cities: list[City]
    workstations_per_employee: float
    workstations_per_workplace: dict[str, float]
    sqm_per_workstation: dict[str, float]
    towers_per_workplace: dict[str, int]
    floors_per_tower: dict[str, int]
    space_audited_share: float
    lease_expiry_window_days: dict[str, int]


class Workforce(_Base):
    departments_l1: list[str]
    l2_per_l1: dict[str, int]
    l3_per_l2: dict[str, int]
    l4_per_l3: dict[str, int]
    team_size: dict[str, float]
    employee_type_mix: dict[str, float]
    employment_status_mix: dict[str, float]
    badge_type_mix: dict[str, float]
    tenure_days: dict[str, float]
    cross_workplace_share: float


class Planted(_Base):
    scheduled_days_per_week: dict[str, int]
    weekday_weights: list[float]
    attendance_rate_by_region: dict[str, float]
    propensity_concentration: float
    off_schedule_attendance_rate: float
    leave: dict[str, Any]
    travel: dict[str, Any]


class Taps(_Base):
    arrival_hour: dict[str, float]
    workday_hours: dict[str, float]
    workplace_arrival_shift_sigma: float
    midday_exit_probability: float
    overnight_worker_share: float
    device_count_per_floor: int


class Defects(_Base):
    hr_resigned_still_active_share: float
    hr_resigned_still_active_days: dict[str, int]
    hr_null_base_city_share: float
    hr_rows_before_hire_date: int
    tap_duplicate_within_90s_share: float
    tap_late_arriving_share: float
    tap_after_termination_share: float
    workplace_null_timezone: int


class DQ(_Base):
    freshness_sla_hours: dict[str, int]
    psi_warn: float
    psi_fail: float
    psi_trailing_days: int
    reconciliation_tolerance: float
    volume_anomaly_z_warn: float
    volume_anomaly_z_fail: float
    tap_after_termination_grace_days: int


class MetricParams(_Base):
    seat_demand_buffer: float
    free_sharing_share: float
    allocated_share_of_delivered: float
    allocated_share_sigma: float
    allocated_share_bounds: list[float]
    cost_per_workstation_month: dict[str, float]


class Config(_Base):
    seed: int
    profile_name: str
    profile: Profile
    geography: Geography
    workforce: Workforce
    planted: Planted
    taps: Taps
    defects: Defects
    dq: DQ
    metrics: MetricParams

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @property
    def start_date(self) -> date:
        from datetime import timedelta

        return self.profile.end_date - timedelta(days=self.profile.n_days - 1)

    @property
    def end_date(self) -> date:
        return self.profile.end_date

    @property
    def cities(self) -> list[City]:
        """Cities in play for this profile, chosen round-robin across regions.

        Taking the first N cities in file order would give the demo profile no
        CN sites at all, and reconciling CN against non-CN column differences is
        the reason the semantic layer exists. Every profile therefore sees every
        region, whatever its size.
        """
        by_region: dict[str, list[City]] = {}
        for city in self.geography.cities:
            by_region.setdefault(city.region, []).append(city)

        chosen: list[City] = []
        regions = sorted(by_region)
        depth = 0
        while len(chosen) < self.profile.n_cities:
            added = False
            for region in regions:
                if depth < len(by_region[region]) and len(chosen) < self.profile.n_cities:
                    chosen.append(by_region[region][depth])
                    added = True
            if not added:
                break
            depth += 1
        return chosen


def _coerce_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def load_config(
    path: str | Path | None = None,
    profile: str | None = None,
) -> Config:
    """Load and validate the simulator config.

    Profile precedence: explicit argument, then ``WDP_PROFILE``, then the
    ``profile:`` key in the YAML itself.
    """
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text())

    profiles = raw.pop("profiles")
    chosen = profile or os.environ.get("WDP_PROFILE") or raw["profile"]
    if chosen not in profiles:
        raise ValueError(f"Unknown profile {chosen!r}; available: {sorted(profiles)}")

    profile_raw = dict(profiles[chosen])
    profile_raw["end_date"] = _coerce_date(profile_raw["end_date"])

    n_defined_cities = len(raw["geography"]["cities"])
    if profile_raw["n_cities"] > n_defined_cities:
        raise ValueError(
            f"Profile {chosen!r} wants {profile_raw['n_cities']} cities but only "
            f"{n_defined_cities} are defined in geography.cities"
        )

    data = dict(raw)
    data["profile"] = Profile(**profile_raw)
    data["profile_name"] = chosen
    return Config(**data)


@lru_cache(maxsize=4)
def get_config(profile: str | None = None) -> Config:
    """Cached loader for callers that do not want to thread config through."""
    return load_config(profile=profile)


# --- Filesystem layout -----------------------------------------------------

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
WAREHOUSE_PATH = REPO_ROOT / "warehouse" / "local.duckdb"
DOCS_DIR = REPO_ROOT / "docs"
IMG_DIR = DOCS_DIR / "img"
SEMANTIC_DIR = REPO_ROOT / "semantic"
METRICS_DIR = SEMANTIC_DIR / "metrics"
DBT_DIR = REPO_ROOT / "dbt"
OUT_DIR = REPO_ROOT / "out"
DEFECT_MANIFEST = RAW_DIR / "_defect_manifest.json"
GROUND_TRUTH = RAW_DIR / "_ground_truth.json"
