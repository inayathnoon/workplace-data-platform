"""Run every registered check and score the result.

The scorecard is deliberately blunt: one status per check, one number, one
sentence. A data quality report that needs interpreting does not get read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import duckdb
import yaml

from ..config import (
    DEFECT_MANIFEST,
    IMG_DIR,
    REPO_ROOT,
    WAREHOUSE_PATH,
    Config,
    load_config,
)
from . import checks as _checks  # noqa: F401  - importing registers the checks
from .base import Category, CheckResult, Status, registered_checks


EXPECTATIONS_PATH = REPO_ROOT / "conf" / "dq_expectations.yml"


def load_expected_failures() -> dict[str, dict]:
    if not EXPECTATIONS_PATH.exists():
        return {}
    raw = yaml.safe_load(EXPECTATIONS_PATH.read_text()) or {}
    return {e["check"]: e for e in raw.get("expected_failures", []) or []}


@dataclass
class Scorecard:
    results: list[CheckResult]
    expected_failures: dict[str, dict] = field(default_factory=dict)

    @property
    def unexpected_failures(self) -> list[CheckResult]:
        """Failures nobody has written down a reason for. These are the ones
        that should stop a pipeline."""
        return [
            r
            for r in self.results
            if r.status is Status.FAIL and r.name not in self.expected_failures
        ]

    @property
    def stale_expectations(self) -> list[str]:
        """Suppressions for checks that now pass - remove them before they hide
        a real failure later."""
        statuses = {r.name: r.status for r in self.results}
        return sorted(
            name
            for name in self.expected_failures
            if statuses.get(name) not in (Status.FAIL, None)
        )

    @property
    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Status}
        for result in self.results:
            out[result.status.value] += 1
        return out

    @property
    def worst(self) -> Status:
        return max((r.status for r in self.results), key=lambda s: s.rank, default=Status.PASS)

    @property
    def passed(self) -> bool:
        """Passing means nothing failed that was not already known about."""
        return not self.unexpected_failures and not self.stale_expectations

    def by_category(self) -> dict[Category, list[CheckResult]]:
        out: dict[Category, list[CheckResult]] = {}
        for result in self.results:
            out.setdefault(result.category, []).append(result)
        return out

    def render(self) -> str:
        lines = []
        for category, results in sorted(self.by_category().items(), key=lambda kv: kv[0].value):
            lines.append(f"\n{category.value.replace('_', ' ').upper()}")
            lines.extend("  " + r.one_line() for r in results)
        counts = self.counts
        expected = counts["fail"] - len(self.unexpected_failures)
        lines.append(
            f"\n{len(self.results)} checks: {counts['pass']} pass, "
            f"{counts['warn']} warn, {counts['fail']} fail "
            f"({expected} expected, {len(self.unexpected_failures)} unexpected)"
        )
        for result in self.unexpected_failures:
            lines.append(f"  UNEXPECTED FAILURE: {result.name} - {result.explanation}")
        for name in self.stale_expectations:
            lines.append(
                f"  STALE EXPECTATION: {name} now passes; remove it from "
                "conf/dq_expectations.yml"
            )
        return "\n".join(lines)


def run_checks(cfg: Config | None = None) -> Scorecard:
    cfg = cfg or load_config()
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        results: list[CheckResult] = []
        for registered in registered_checks():
            outcome = registered.fn(con, cfg)
            results.extend(outcome if isinstance(outcome, list) else [outcome])
    finally:
        con.close()
    return Scorecard(results=results, expected_failures=load_expected_failures())


# --- Defect recall ---------------------------------------------------------


def defect_recall(scorecard: Scorecard) -> list[dict]:
    """Score the DQ suite against the defects the generator says it injected.

    Without this, a green scorecard proves only that the checks ran. The
    generator wrote down exactly how many of each defect it planted, so the
    checks can be graded on whether they found them - which is the difference
    between a data quality framework and a wall of green ticks.
    """
    if not DEFECT_MANIFEST.exists():
        return []
    planted = json.loads(DEFECT_MANIFEST.read_text())
    found = {r.name: r for r in scorecard.results}

    rows = []

    def add(defect: str, injected: int, check_name: str, detected: float | None) -> None:
        rows.append(
            {
                "defect": defect,
                "injected": injected,
                "detected_by": check_name,
                "detected": None if detected is None else int(detected),
                "caught": detected is not None and detected > 0,
            }
        )

    active = found.get("active_after_termination")
    add(
        "HR: resigned employees still reported active",
        planted.get("hr_resigned_still_active_employees", 0),
        "active_after_termination",
        None if active is None else active.details.get("employees"),
    )
    early = found.get("snapshot_before_hire_date")
    add(
        "HR: snapshot rows before the employee's hire date",
        planted.get("hr_rows_before_hire_date", 0),
        "snapshot_before_hire_date",
        None if early is None else early.value,
    )
    tz = found.get("missing_workplace_timezone")
    add(
        "Space: workplaces with no timezone",
        planted.get("workplace_null_timezone_workplaces", 0),
        "missing_workplace_timezone",
        None if tz is None else tz.value,
    )
    taps = found.get("tap_after_termination")
    add(
        "Badge: taps after termination (any lag)",
        planted.get("tap_after_termination", 0),
        "tap_after_termination",
        None if taps is None else taps.details.get("taps_after_termination_any"),
    )
    return rows


def render_defect_recall(rows: list[dict]) -> str:
    if not rows:
        return "No defect manifest found; run `make data` first."
    width = max(len(r["defect"]) for r in rows)
    lines = [f"{'Planted defect'.ljust(width)}  {'injected':>9}  {'detected':>9}  caught"]
    for row in rows:
        detected = "-" if row["detected"] is None else f"{row['detected']:,}"
        lines.append(
            f"{row['defect'].ljust(width)}  {row['injected']:>9,}  {detected:>9}  "
            f"{'yes' if row['caught'] else 'NO'}"
        )
    return "\n".join(lines)


# --- Scorecard chart -------------------------------------------------------


def render_scorecard(scorecard: Scorecard, path=None):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = path or IMG_DIR / "dq_scorecard.png"
    by_category = scorecard.by_category()
    order = sorted(by_category, key=lambda c: c.value)
    colours = {Status.PASS: "#5f9e6e", Status.WARN: "#d9a441", Status.FAIL: "#c1544a"}

    rows = sum(len(by_category[c]) for c in order) + len(order)
    fig, ax = plt.subplots(figsize=(11, max(4.0, rows * 0.34)))

    y = 0.0
    yticks, ylabels = [], []
    for category in order:
        ax.text(-0.02, -y, category.value.replace("_", " ").upper(), fontsize=9,
                fontweight="bold", color="#33404f", ha="right", va="center",
                transform=ax.get_yaxis_transform())
        y += 1
        for result in by_category[category]:
            value = 1.0 if result.value is None else max(float(result.value), 0.0)
            ax.barh(-y, max(value, 0.4) if value else 0.4, color=colours[result.status],
                    height=0.62, edgecolor="none")
            yticks.append(-y)
            ylabels.append(result.name)
            y += 1

    ax.set_yticks(yticks)
    ax.set_yticklabels(ylabels, fontsize=8)
    ax.set_xscale("symlog")
    ax.set_xlabel("check value (symlog; bar colour is the verdict)", fontsize=9)
    counts = scorecard.counts
    ax.set_title(
        f"Data quality scorecard - {counts['pass']} pass, {counts['warn']} warn, "
        f"{counts['fail']} fail",
        fontsize=12, pad=14,
    )
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", alpha=0.25)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in colours.values()]
    ax.legend(handles, [s.value for s in colours], loc="lower right", fontsize=8, frameon=False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
    return path


if __name__ == "__main__":  # pragma: no cover
    import sys

    card = run_checks()
    print(card.render())
    print()
    print(render_defect_recall(defect_recall(card)))
    render_scorecard(card)
    sys.exit(0 if card.passed else 1)
