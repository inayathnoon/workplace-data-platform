"""Source system 2: ``badge_taps``.

The high-volume source. Taps are emitted in workplace-local time and stored in
UTC, which is the whole reason the warehouse has to carry both a UTC timestamp
and a workplace-local date: an 01:20 tap in Ironwold and an 01:20 tap in
Aurelia belong to different local days, and attendance is a local-day concept.

Generation is vectorised per day so the same code path serves the 14-day demo
and the 180-day, 35M-row full profile without a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from ..config import Config
from .rng import lognormal_from_mean, substream

# Probability a travelling employee taps into a workplace in the city they are
# visiting. The rest are with customers, in transit, or working from a hotel.
TRAVEL_TAP_PROBABILITY = 0.60

# Evening start for the overnight cohort, in workplace-local hours.
OVERNIGHT_START_HOUR = 17.5


@dataclass
class TapDefectCounts:
    duplicates: int = 0
    late_arriving: int = 0
    after_termination: int = 0
    # Employees, not rows. The row count is not stable end to end: a
    # post-termination tap can itself be duplicated by the re-swipe injector,
    # and staging then drops the duplicates that fall inside the 90-second
    # window. The number of people whose badge still worked survives both.
    after_termination_employees: set = field(default_factory=set)

    def as_dict(self) -> dict[str, int]:
        return {
            "tap_duplicates_within_90s": self.duplicates,
            "tap_late_arriving": self.late_arriving,
            "tap_after_termination_rows": self.after_termination,
            "tap_after_termination_employees": len(self.after_termination_employees),
        }


@dataclass
class AttendanceTally:
    """Realised attendance, accumulated as days are generated.

    The config plants a target attendance rate per region, but eligibility,
    leave and travel all subtract from it, so the rate the simulator actually
    realises is not the rate in the config. Both go into ground_truth.json and
    the README compares the recovered number against the realised one.
    """

    scheduled_eligible: dict[str, int] = field(default_factory=dict)
    scheduled_attended: dict[str, int] = field(default_factory=dict)

    def add(self, region: str, eligible: int, attended: int) -> None:
        self.scheduled_eligible[region] = self.scheduled_eligible.get(region, 0) + eligible
        self.scheduled_attended[region] = self.scheduled_attended.get(region, 0) + attended

    def realised_rates(self) -> dict[str, float]:
        return {
            r: round(self.scheduled_attended.get(r, 0) / n, 4)
            for r, n in self.scheduled_eligible.items()
            if n
        }


class TapGenerator:
    """Holds the per-employee and per-workplace lookups the day loop needs."""

    def __init__(self, cfg: Config, master: pd.DataFrame, workplaces: pd.DataFrame) -> None:
        self.cfg = cfg
        self.master = master.reset_index(drop=True)
        self.rng = substream(cfg.seed, "taps")
        self.defects = TapDefectCounts()
        self.tally = AttendanceTally()

        n = len(self.master)
        self.emp_index = {e: i for i, e in enumerate(self.master["emp_id"])}

        self.hire = self.master["hire_date"].to_numpy()
        self.term = self.master["term_date"].to_numpy()
        self.assign_start = self.master["assignment_start"].to_numpy()
        self.assign_end = self.master["assignment_end"].to_numpy()
        self.propensity = self.master["attendance_propensity"].to_numpy()
        self.overnight = self.master["is_overnight_worker"].to_numpy()
        self.arrival_offset = self.master["arrival_offset_hours"].to_numpy()
        self.region = self.master["region"].to_numpy()

        self.schedule = np.zeros((n, 7), dtype=bool)
        for i, days in enumerate(self.master["scheduled_weekdays"]):
            for d in days:
                self.schedule[i, d] = True

        # Workplace lookups.
        self.workplace_tz = (
            workplaces.drop_duplicates("workplace_code")
            .set_index("workplace_code")["timezone"]
            .to_dict()
        )
        self.city_primary_workplace = (
            workplaces.groupby(["city", "workplace_code"], as_index=False)["delivered_workstations"]
            .sum()
            .sort_values("delivered_workstations", ascending=False)
            .drop_duplicates("city")
            .set_index("city")["workplace_code"]
            .to_dict()
        )
        self.floors: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        for code, grp in workplaces.groupby("workplace_code"):
            weights = grp["delivered_workstations"].to_numpy(dtype=float)
            self.floors[code] = (
                grp["tower"].to_numpy(),
                grp["floor"].to_numpy(),
                weights / weights.sum(),
            )

        shift_sigma = cfg.taps.workplace_arrival_shift_sigma
        codes = sorted(self.floors)
        self.workplace_shift = dict(
            zip(codes, self.rng.normal(0.0, shift_sigma, len(codes)), strict=True)
        )

        self.home_workplace = self.master["theory_workplace"].to_numpy()

    # --- per-day masks -----------------------------------------------------

    def _eligible_mask(self, day: date) -> np.ndarray:
        pre_hire = np.array([day < h for h in self.hire])
        post_term = np.array([t is not None and day > t for t in self.term])
        return ~pre_hire & ~post_term

    def _index_mask(self, keys: set[str]) -> np.ndarray:
        mask = np.zeros(len(self.master), dtype=bool)
        if not keys:
            return mask
        idx = [self.emp_index[e] for e in keys if e in self.emp_index]
        mask[idx] = True
        return mask

    def generate_day(
        self,
        day: date,
        leave_today: set[str],
        travel_today: dict[str, str],
    ) -> pd.DataFrame:
        cfg = self.cfg
        weekday = day.weekday()

        eligible = self._eligible_mask(day)
        on_leave = self._index_mask(leave_today)
        travelling = self._index_mask(set(travel_today))
        scheduled = self.schedule[:, weekday] & (weekday < 5)

        # Someone posted to another city is not available to their home
        # workplace at all, so they neither attend nor count against it.
        on_assignment = np.array(
            [
                s is not None and e is not None and s <= day <= e
                for s, e in zip(self.assign_start, self.assign_end, strict=True)
            ]
        )

        base_p = np.where(scheduled, self.propensity, cfg.planted.off_schedule_attendance_rate)
        can_attend = eligible & ~on_leave & ~on_assignment

        draw = self.rng.random(len(self.master))
        attends_home = can_attend & ~travelling & (draw < base_p)
        attends_away = can_attend & travelling & (draw < TRAVEL_TAP_PROBABILITY)

        # Record realised attendance on scheduled office days, by region.
        #
        # The denominator is days on which attendance was actually owed:
        # employed, scheduled, not on leave, not travelling, not on assignment.
        # It has to match `is_attendance_expected` in int_employee_day exactly,
        # or the planted-vs-recovered table in the README compares two
        # different quantities and the difference gets read as pipeline error.
        denom_mask = can_attend & scheduled & ~travelling
        for region in np.unique(self.region):
            r = self.region == region
            self.tally.add(
                str(region),
                int((denom_mask & r).sum()),
                int((attends_home & scheduled & r).sum()),
            )

        attend_idx = np.flatnonzero(attends_home | attends_away)
        if attend_idx.size == 0:
            return pd.DataFrame(columns=_TAP_COLUMNS)

        workplace = self.home_workplace[attend_idx].copy()
        away = attends_away[attend_idx]
        for pos in np.flatnonzero(away):
            emp = self.master["emp_id"].iloc[attend_idx[pos]]
            dest_city = travel_today[emp]
            workplace[pos] = self.city_primary_workplace.get(dest_city, workplace[pos])

        frame = self._taps_for_attendees(day, attend_idx, workplace)
        frame = self._inject_post_termination_taps(day, frame)
        frame = self._inject_duplicates(frame)
        frame = self._apply_reporting_lateness(frame)
        return frame

    # --- tap construction --------------------------------------------------

    def _taps_for_attendees(
        self, day: date, attend_idx: np.ndarray, workplace: np.ndarray
    ) -> pd.DataFrame:
        cfg = self.cfg
        n = len(attend_idx)
        rng = self.rng

        shifts = np.array([self.workplace_shift.get(c, 0.0) for c in workplace])
        arrival = (
            rng.normal(cfg.taps.arrival_hour["mean"], cfg.taps.arrival_hour["sigma"], n)
            + shifts
            + self.arrival_offset[attend_idx]
        )
        is_overnight = self.overnight[attend_idx]
        arrival = np.where(is_overnight, OVERNIGHT_START_HOUR + rng.normal(0, 0.8, n), arrival)
        arrival = np.clip(arrival, 4.0, 22.0)

        duration = np.maximum(
            cfg.taps.workday_hours["min"],
            lognormal_from_mean(
                rng, cfg.taps.workday_hours["mean"], cfg.taps.workday_hours["sigma"], n
            ),
        )
        # Overnight workers work into the small hours of the next local day.
        duration = np.where(is_overnight, np.maximum(duration, 7.0), duration)

        tower, floor, device = self._pick_floors(workplace, rng)

        emp_ids = self.master["emp_id"].to_numpy()[attend_idx]

        records: list[dict] = []

        def push(i: int, hour: float, direction: str) -> None:
            records.append(
                {
                    "emp_id": emp_ids[i],
                    "workplace_code": workplace[i],
                    "tower": tower[i],
                    "floor": int(floor[i]),
                    "local_hour": float(hour),
                    "direction": direction,
                    "device_id": device[i],
                }
            )

        midday = rng.random(n) < cfg.taps.midday_exit_probability
        lunch_offset = rng.uniform(3.0, 5.0, n)
        lunch_len = rng.uniform(0.4, 1.4, n)

        for i in range(n):
            push(i, arrival[i], "in")
            if midday[i] and lunch_offset[i] + lunch_len[i] < duration[i]:
                push(i, arrival[i] + lunch_offset[i], "out")
                push(i, arrival[i] + lunch_offset[i] + lunch_len[i], "in")
            push(i, arrival[i] + duration[i], "out")

        frame = pd.DataFrame.from_records(records)
        frame["tap_ts_utc"] = self._local_hours_to_utc(day, frame)
        frame["is_post_termination"] = False
        return frame[_TAP_COLUMNS].copy()

    def _pick_floors(
        self, workplace: np.ndarray, rng: np.random.Generator
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        tower = np.empty(len(workplace), dtype=object)
        floor = np.empty(len(workplace), dtype=int)
        device = np.empty(len(workplace), dtype=object)
        for code in np.unique(workplace):
            pos = np.flatnonzero(workplace == code)
            towers, floors, weights = self.floors[code]
            picks = rng.choice(len(towers), size=len(pos), p=weights)
            tower[pos] = towers[picks]
            floor[pos] = floors[picks]
            dev = rng.integers(1, self.cfg.taps.device_count_per_floor + 1, size=len(pos))
            device[pos] = [
                f"{code}-{t}-{f:02d}-D{d}"
                for t, f, d in zip(towers[picks], floors[picks], dev, strict=True)
            ]
        return tower, floor, device

    def _local_hours_to_utc(self, day: date, frame: pd.DataFrame) -> pd.Series:
        """Convert workplace-local hour offsets to UTC, one timezone at a time."""
        midnight = datetime.combine(day, time(0, 0))
        out = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
        tz_of = frame["workplace_code"].map(self.workplace_tz)
        # A workplace with a missing timezone is a real defect in the source; we
        # fall back to UTC here and let the DQ layer report it rather than
        # silently guessing a sensible zone.
        tz_of = tz_of.fillna("UTC")
        for tz, idx in frame.groupby(tz_of).groups.items():
            local = pd.to_datetime(
                [midnight + timedelta(hours=float(h)) for h in frame.loc[idx, "local_hour"]]
            )
            converted = (
                local.tz_localize(tz, ambiguous=True, nonexistent="shift_forward")
                .tz_convert("UTC")
                .tz_localize(None)
            )
            out.loc[idx] = converted
        return out

    # --- defect injection --------------------------------------------------

    def _inject_post_termination_taps(self, day: date, frame: pd.DataFrame) -> pd.DataFrame:
        """Taps from employees who left - a revoked-access failure, and the
        check most likely to be asked about by a security reviewer."""
        share = self.cfg.defects.tap_after_termination_share
        if share <= 0:
            return frame
        recently_gone = [
            i
            for i, t in enumerate(self.term)
            if t is not None and t < day <= t + timedelta(days=14)
        ]
        if not recently_gone:
            return frame
        n_pick = self.rng.binomial(len(recently_gone), min(share * 20, 1.0))
        if n_pick == 0:
            return frame
        picked = self.rng.choice(recently_gone, size=int(n_pick), replace=False)
        extra = self._taps_for_attendees(
            day, np.asarray(picked), self.home_workplace[picked].copy()
        )
        extra["is_post_termination"] = True
        self.defects.after_termination += len(extra)
        self.defects.after_termination_employees.update(extra["emp_id"].unique())
        return pd.concat([frame, extra], ignore_index=True)

    def _inject_duplicates(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Re-emit a share of taps a few seconds later.

        Badge readers do this when someone swipes twice because the door was
        slow. De-duplication in staging must collapse them, and must do it on a
        time window rather than on exact-timestamp equality.
        """
        share = self.cfg.defects.tap_duplicate_within_90s_share
        if share <= 0 or frame.empty:
            return frame
        n_dupe = self.rng.binomial(len(frame), share)
        if n_dupe == 0:
            return frame
        idx = self.rng.choice(len(frame), size=int(n_dupe), replace=False)
        dupes = frame.iloc[idx].copy()
        jitter = self.rng.integers(5, 90, size=len(dupes))
        dupes["tap_ts_utc"] = [
            ts + timedelta(seconds=int(s))
            for ts, s in zip(dupes["tap_ts_utc"], jitter, strict=True)
        ]
        self.defects.duplicates += len(dupes)
        return pd.concat([frame, dupes], ignore_index=True)

    def _apply_reporting_lateness(self, frame: pd.DataFrame) -> pd.DataFrame:
        """A share of rows are reported a day after they happened."""
        if frame.empty:
            frame["ingested_date"] = pd.Series(dtype="object")
            return frame
        share = self.cfg.defects.tap_late_arriving_share
        late = self.rng.random(len(frame)) < share
        tap_day = pd.to_datetime(frame["tap_ts_utc"]).dt.date
        frame["ingested_date"] = [
            d + timedelta(days=1) if is_late else d
            for d, is_late in zip(tap_day, late, strict=True)
        ]
        self.defects.late_arriving += int(late.sum())
        return frame


_TAP_COLUMNS = [
    "emp_id",
    "workplace_code",
    "tower",
    "floor",
    "tap_ts_utc",
    "direction",
    "device_id",
    "local_hour",
    "is_post_termination",
]
