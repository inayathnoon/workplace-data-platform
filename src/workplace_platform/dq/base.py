"""Check registry and result types.

Every check returns the same shape: a status, the number that produced it, the
threshold it was judged against, a one-line explanation in business language,
and a sample of offending rows. The explanation is not decoration - a data
quality report that says ``check_47 FAILED`` gets ignored, and one that says
"31 people badged in after their last working day" gets acted on.

Statuses are three-valued on purpose. Real feeds are never perfectly clean, and
a framework with only pass and fail trains people to ignore the failures.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import duckdb
import pandas as pd


class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"

    @property
    def rank(self) -> int:
        return {"pass": 0, "warn": 1, "fail": 2}[self.value]


class Category(StrEnum):
    FRESHNESS = "freshness"
    INTEGRITY = "referential_integrity"
    GRAIN = "grain_uniqueness"
    LEGALITY = "status_transition"
    DRIFT = "distribution_drift"
    RECONCILIATION = "reconciliation"
    VOLUME = "volume_anomaly"


@dataclass
class CheckResult:
    name: str
    category: Category
    status: Status
    explanation: str
    value: float | None = None
    threshold: float | None = None
    sample: pd.DataFrame | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def sample_rows(self) -> int:
        return 0 if self.sample is None else len(self.sample)

    def one_line(self) -> str:
        value = "-" if self.value is None else f"{self.value:,.4g}"
        return f"[{self.status.value.upper():4s}] {self.name:38s} {value:>12s}  {self.explanation}"


@dataclass
class Check:
    name: str
    category: Category
    description: str
    fn: Callable[..., CheckResult | list[CheckResult]]


_REGISTRY: dict[str, Check] = {}


def check(name: str, category: Category, description: str):
    """Register a check. Duplicate names are an error, not a last-one-wins."""

    def decorator(fn):
        if name in _REGISTRY:
            raise ValueError(f"duplicate check {name!r}")
        _REGISTRY[name] = Check(name=name, category=category, description=description, fn=fn)
        return fn

    return decorator


def registered_checks() -> list[Check]:
    return sorted(_REGISTRY.values(), key=lambda c: (c.category.value, c.name))


def query(con: duckdb.DuckDBPyConnection, sql: str, params: Iterable | None = None) -> pd.DataFrame:
    return con.execute(sql, list(params or [])).df()


def grade(value: float, warn: float, fail: float, higher_is_worse: bool = True) -> Status:
    """Three-valued grading against two thresholds."""
    if higher_is_worse:
        if value >= fail:
            return Status.FAIL
        return Status.WARN if value >= warn else Status.PASS
    if value <= fail:
        return Status.FAIL
    return Status.WARN if value <= warn else Status.PASS
