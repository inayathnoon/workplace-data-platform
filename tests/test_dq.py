"""The data quality suite, graded on what it catches."""

from __future__ import annotations

import json

import numpy as np
import pytest

from workplace_platform.config import DEFECT_MANIFEST
from workplace_platform.dq.base import Status, grade, registered_checks
from workplace_platform.dq.checks import _psi
from workplace_platform.dq.runner import defect_recall, load_expected_failures, run_checks


@pytest.fixture(scope="module")
def card():
    return run_checks()


def test_every_check_family_is_registered():
    categories = {c.category for c in registered_checks()}
    assert len(categories) == 7, "the brief asks for seven check families"


def test_grading_is_three_valued():
    assert grade(0.0, warn=0.1, fail=0.25) is Status.PASS
    assert grade(0.15, warn=0.1, fail=0.25) is Status.WARN
    assert grade(0.30, warn=0.1, fail=0.25) is Status.FAIL


def test_psi_of_a_distribution_against_itself_is_zero():
    rng = np.random.default_rng(0)
    sample = rng.normal(size=5_000)
    assert _psi(sample, sample) == pytest.approx(0.0, abs=1e-9)


def test_psi_does_not_cry_wolf_on_a_small_sample():
    """Ten buckets on forty points reports drift between identical
    distributions. Bucket count is capped by sample size for that reason."""
    rng = np.random.default_rng(0)
    a = rng.normal(size=40)
    b = rng.normal(size=40)
    assert _psi(a, b) < 0.25


def test_psi_detects_a_real_shift():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1, 5_000)
    b = rng.normal(1.5, 1, 5_000)
    assert _psi(a, b) > 0.25


def test_every_result_carries_an_explanation(card):
    for result in card.results:
        assert result.explanation.strip(), result.name
        assert not result.explanation.startswith(result.name)


def test_all_planted_defects_are_detected(card):
    """The suite is graded on recall against the manifest, not on being green."""
    rows = defect_recall(card)
    assert rows, "no defect manifest"
    missed = [r["defect"] for r in rows if not r["caught"]]
    assert not missed, f"undetected planted defects: {missed}"


def test_detected_counts_match_injected_counts(card):
    for row in defect_recall(card):
        assert row["detected"] == row["injected"], row["defect"]


def test_reconciliation_is_exact(card):
    for result in card.results:
        if result.name.startswith("reconcile_"):
            assert result.status is Status.PASS, result.explanation


def test_only_expected_failures_fail(card):
    assert [r.name for r in card.unexpected_failures] == []


def test_no_stale_expectations(card):
    assert card.stale_expectations == []


def test_every_expectation_has_an_owner_and_a_reason():
    for name, entry in load_expected_failures().items():
        assert entry.get("reason", "").strip(), name
        assert entry.get("owner", "").strip(), name
        assert entry.get("review_by"), name


def test_manifest_records_every_defect_family():
    planted = json.loads(DEFECT_MANIFEST.read_text())
    for key in (
        "hr_resigned_still_active_employees",
        "hr_rows_before_hire_date",
        "tap_duplicates_within_90s",
        "tap_late_arriving",
        "tap_after_termination_rows",
        "tap_after_termination_employees",
        "workplace_null_timezone_workplaces",
    ):
        assert planted.get(key, 0) > 0, key
