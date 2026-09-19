"""Properties of the simulator that the rest of the repo relies on."""

from __future__ import annotations

from datetime import timedelta

import pytest

from workplace_platform.config import load_config
from workplace_platform.gen.employees import generate_employees, status_on
from workplace_platform.gen.leave import generate_leave, leave_days
from workplace_platform.gen.rng import beta_around, lognormal_from_mean, substream
from workplace_platform.gen.workplaces import generate_workplaces


@pytest.fixture(scope="module")
def small_cfg():
    return load_config(profile="demo")


@pytest.fixture(scope="module")
def workplaces(small_cfg):
    return generate_workplaces(small_cfg)


@pytest.fixture(scope="module")
def employees(small_cfg, workplaces):
    return generate_employees(small_cfg, workplaces)


def test_substreams_are_independent(small_cfg):
    """Adding a source must not move the numbers another source produces."""
    a1 = substream(small_cfg.seed, "taps").random(50)
    a2 = substream(small_cfg.seed, "taps").random(50)
    b = substream(small_cfg.seed, "leave").random(50)
    assert (a1 == a2).all()
    assert not (a1 == b).all()


def test_lognormal_hits_the_arithmetic_mean():
    """The config says `mean: 1100` and expects the arithmetic mean, not the
    mean of the underlying normal."""
    rng = substream(1, "t")
    draws = lognormal_from_mean(rng, 1000.0, 0.45, 200_000)
    assert draws.mean() == pytest.approx(1000.0, rel=0.02)


def test_beta_around_hits_its_target_mean():
    rng = substream(1, "t")
    draws = beta_around(rng, 0.62, 12.0, 100_000)
    assert draws.mean() == pytest.approx(0.62, abs=0.01)


def test_generation_is_reproducible(small_cfg):
    first = generate_workplaces(small_cfg)
    second = generate_workplaces(small_cfg)
    assert first.equals(second)


def test_cn_allocation_already_includes_the_sharing_pool(workplaces):
    """The regional source difference the semantic layer exists to reconcile.
    If this stops being true, the CN variant is reconciling nothing."""
    cn = workplaces[workplaces["region"] == "CN"]
    other = workplaces[workplaces["region"] != "CN"]
    assert not cn.empty and not other.empty
    cn_share = (cn["allocated_workstations"] / cn["delivered_workstations"]).mean()
    other_share = (other["allocated_workstations"] / other["delivered_workstations"]).mean()
    assert cn_share > other_share


def test_status_precedence_termination_beats_everything():
    hire = __import__("datetime").date(2024, 1, 1)
    term = __import__("datetime").date(2025, 6, 1)
    after = term + timedelta(days=3)
    assert status_on(hire, term, "resigned", None, None, after) == "resigned"
    # Even with an assignment covering the day.
    assert status_on(hire, term, "terminated", hire, after, after) == "terminated"


def test_status_before_hire_is_onboarding():
    import datetime

    hire = datetime.date(2025, 6, 10)
    assert status_on(hire, None, "active", None, None, datetime.date(2025, 6, 1)) == "onboarding"


def test_leave_expansion_ignores_unapproved_requests(small_cfg, employees):
    leave = generate_leave(small_cfg, employees)
    approved = leave[leave["status"] == "approved"]
    assert len(approved) < len(leave), "some requests should be pending or cancelled"
    days = leave_days(leave)
    approved_ids = set(approved["emp_id"])
    assert {emp for emp, _ in days} <= approved_ids


def test_leave_days_are_deduplicated_across_overlapping_requests(small_cfg, employees):
    """Overlapping requests are injected on purpose; expanding them naively
    would double-count the overlap."""
    leave = generate_leave(small_cfg, employees)
    days = leave_days(leave)
    assert len(days) == len(set(days))


def test_every_employee_has_a_schedule(employees):
    assert employees["scheduled_weekdays"].map(len).min() >= 3
    assert employees["scheduled_weekdays"].map(lambda d: max(d) <= 4).all()
