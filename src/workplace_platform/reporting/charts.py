"""Static charts for the README, written to docs/img/.

Rendered to PNG by a script rather than screenshotted from a dashboard, so the
README renders without anyone running the pipeline, and every chart is
reproducible from the seed.

Colour follows a validated categorical palette: hues are assigned in fixed slot
order and never cycled, magnitude uses a single hue light-to-dark, and every
chart carries direct value labels rather than relying on colour alone.
"""

from __future__ import annotations

import json

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from ..config import GROUND_TRUTH, IMG_DIR, WAREHOUSE_PATH, Config, load_config  # noqa: E402

# Categorical slots, in fixed order. Validated for adjacent-pair separation
# under colour-vision deficiency on a light surface.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
# Single hue, light to dark, for magnitude.
SEQUENTIAL = ["#cfe0f5", "#9ec2ea", "#6ba3e0", "#2a78d6", "#1a4d8a"]

INK = "#0b0b0b"
INK_MUTED = "#52514e"
SURFACE = "#fcfcfb"
GRID = "#d8d8d4"


def _style(ax, title: str, subtitle: str = "") -> None:
    ax.set_facecolor(SURFACE)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, alpha=0.6, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, fontsize=13, color=INK, pad=18 if subtitle else 10, loc="left")
    if subtitle:
        ax.text(
            0, 1.02, subtitle, transform=ax.transAxes, fontsize=9.5, color=INK_MUTED, va="bottom"
        )


def _save(fig, name: str):
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    path = IMG_DIR / name
    fig.savefig(path, dpi=150, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return path


# --- 1. Planted versus recovered -------------------------------------------


def chart_planted_vs_recovered(con, cfg: Config):
    """The headline chart: does the pipeline recover what the simulator planted?

    Two bars per region rather than one bar of error, because the reader's first
    question is "what were the numbers", not "how big was the gap". The gap is
    labelled on top.
    """
    from ..semantic.compile import compile_metric
    from ..semantic.registry import load_registry

    compiled = compile_metric(load_registry(), "attendance_rate", ["region"], None, cfg=cfg)
    frame = con.execute(compiled.sql).df()
    truth = json.loads(GROUND_TRUTH.read_text())["planted"]["attendance_rate_by_region_realised"]
    frame["planted"] = frame["region"].map(truth)
    frame = frame.sort_values("region")

    fig, ax = plt.subplots(figsize=(9, 4.6))
    x = range(len(frame))
    width = 0.38
    ax.bar(
        [i - width / 2 for i in x],
        frame["planted"],
        width,
        label="Planted (simulator)",
        color=SERIES[0],
        edgecolor=SURFACE,
        linewidth=2,
    )
    ax.bar(
        [i + width / 2 for i in x],
        frame["attendance_rate"],
        width,
        label="Recovered (pipeline)",
        color=SERIES[1],
        edgecolor=SURFACE,
        linewidth=2,
    )
    for i, (_, row) in enumerate(frame.iterrows()):
        ax.text(
            i - width / 2,
            row["planted"] + 0.012,
            f"{row['planted']:.4f}",
            ha="center",
            fontsize=8.5,
            color=INK_MUTED,
        )
        ax.text(
            i + width / 2,
            row["attendance_rate"] + 0.012,
            f"{row['attendance_rate']:.4f}",
            ha="center",
            fontsize=8.5,
            color=INK_MUTED,
        )
        error = abs(row["attendance_rate"] - row["planted"])
        ax.text(
            i,
            max(row["planted"], row["attendance_rate"]) + 0.055,
            f"error {error:.5f}",
            ha="center",
            fontsize=8,
            color=INK,
        )

    ax.set_xticks(list(x))
    ax.set_xticklabels(frame["region"])
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("attendance rate", color=INK_MUTED, fontsize=9.5)
    _style(
        ax,
        "Planted vs recovered attendance rate, by region",
        "Recovered through the compiled metric, not a bespoke query. "
        "Planted is the rate the simulator realised.",
    )
    ax.legend(frameon=False, fontsize=9, loc="upper right", labelcolor=INK_MUTED)
    return _save(fig, "planted_vs_recovered.png")


# --- 2. Employee-day state composition -------------------------------------

# Labels say "on a scheduled day" explicitly. The cascade resolves a weekend
# tap to non_office_day, not to attended, so a band labelled plain "Attended"
# reads as though nobody ever came in at a weekend.
STATE_GROUPS = {
    "Attended on a scheduled day": ["attended"],
    "No-show on a scheduled day": ["no_show"],
    "Not a scheduled day (any taps counted here)": ["non_office_day"],
    "Excused (leave, travel, assignment)": ["on_leave", "on_travel", "on_assignment"],
    "Not employed that day": ["pre_hire", "post_exit"],
}


def chart_employee_day_states(con, cfg: Config):
    """Where every employee-day went, every day.

    Five groups rather than the cascade's eight states: leave, travel and
    assignment are one idea to a reader ("excused"), and eight stacked bands is
    past the point where anyone can follow a colour back to a legend.
    """
    frame = con.execute(
        """
        select local_date, employee_day_state, count(*) as employee_days
        from main_intermediate.int_employee_day
        group by 1, 2 order by 1
        """
    ).df()
    frame["group"] = frame["employee_day_state"].map(
        {state: group for group, states in STATE_GROUPS.items() for state in states}
    )
    pivot = frame.groupby(["local_date", "group"])["employee_days"].sum().unstack(fill_value=0)
    pivot = pivot[[g for g in STATE_GROUPS if g in pivot.columns]]

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.stackplot(
        pd.to_datetime(pivot.index),
        [pivot[c] for c in pivot.columns],
        labels=list(pivot.columns),
        colors=SERIES[: len(pivot.columns)],
        edgecolor=SURFACE,
        linewidth=2,
    )
    ax.set_ylabel("employee-days", color=INK_MUTED, fontsize=9.5)
    ax.set_xlim(pd.to_datetime(pivot.index).min(), pd.to_datetime(pivot.index).max())
    _style(
        ax,
        "Every employee-day, resolved to exactly one state",
        "The precedence cascade in int_employee_day. Weekend dips are the "
        "non-scheduled band expanding, not attendance collapsing.",
    )
    ax.legend(
        frameon=False,
        fontsize=9,
        loc="upper center",
        ncol=2,
        bbox_to_anchor=(0.5, -0.26),
        labelcolor=INK_MUTED,
    )
    fig.autofmt_xdate(rotation=30, ha="right")
    return _save(fig, "employee_day_states.png")


# --- 3. Supply against demand ----------------------------------------------


def chart_supply_vs_demand(con, cfg: Config):
    """The supply ladder, per region.

    One hue, light to dark, because these are five magnitudes of the same thing
    rather than five categories. Reading down the ladder is the argument: the
    distance between what is built and what is used is the whole subject.
    """
    frame = con.execute(
        """
        with per_workplace as (
            select region, workplace_code,
                   max(delivered_workstations) as delivered,
                   max(allocated_workstations) as allocated,
                   max(available_workstations) as available,
                   max(attendance_actual) filter (where not is_weekend) as peak_attendance,
                   avg(attendance_actual) filter (where not is_weekend) as mean_attendance
            from main_marts.fct_workplace_capacity_daily
            group by 1, 2
        )
        select region,
               sum(delivered) as delivered,
               sum(allocated) as allocated,
               sum(available) as available,
               sum(peak_attendance) as peak_attendance,
               sum(mean_attendance) as mean_attendance
        from per_workplace group by 1 order by 1
        """
    ).df()

    steps = [
        ("Delivered desks", "delivered"),
        ("Allocated", "allocated"),
        ("Available (allocated + sharing)", "available"),
        ("Peak weekday attendance", "peak_attendance"),
        ("Mean weekday attendance", "mean_attendance"),
    ]
    fig, ax = plt.subplots(figsize=(10, 5.4))
    height = 0.15
    for slot, (label, column) in enumerate(steps):
        # Negated so the ladder reads top-down: delivered first, used last.
        offsets = [i - (slot - 2) * height for i in range(len(frame))]
        ax.barh(
            offsets,
            frame[column],
            height,
            label=label,
            color=SEQUENTIAL[slot],
            edgecolor=SURFACE,
            linewidth=2,
        )
        for y, value in zip(offsets, frame[column], strict=True):
            ax.text(
                value + max(frame["delivered"]) * 0.01,
                y,
                f"{value:,.0f}",
                va="center",
                fontsize=8,
                color=INK_MUTED,
            )

    ax.set_yticks(range(len(frame)))
    ax.set_yticklabels(frame["region"])
    ax.set_xlabel("workstations", color=INK_MUTED, fontsize=9.5)
    ax.set_xlim(0, float(frame["delivered"].max()) * 1.18)
    ax.grid(axis="x", color=GRID, alpha=0.6, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    _style(
        ax,
        "Supply against demand, by region",
        "CN available equals allocated: its free-sharing pool is already inside "
        "the allocated column. That reconciliation lives in the metric registry.",
    )
    ax.legend(
        frameon=False,
        fontsize=8.5,
        loc="upper center",
        ncol=3,
        bbox_to_anchor=(0.5, -0.13),
        labelcolor=INK_MUTED,
    )
    return _save(fig, "supply_vs_demand.png")


# --- Entry point -----------------------------------------------------------


def render_all(cfg: Config | None = None) -> list:
    cfg = cfg or load_config()
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        paths = [
            chart_planted_vs_recovered(con, cfg),
            chart_employee_day_states(con, cfg),
            chart_supply_vs_demand(con, cfg),
        ]
    finally:
        con.close()

    # The lineage graph and DQ scorecard are owned by the modules that produce
    # them, so they stay correct when those modules change.
    from ..dq.runner import render_scorecard, run_checks
    from ..semantic.compile import render_lineage
    from ..semantic.registry import load_registry

    paths.append(render_lineage(load_registry(), IMG_DIR / "metric_lineage.png"))
    paths.append(render_scorecard(run_checks(cfg)))
    return paths


if __name__ == "__main__":  # pragma: no cover
    for path in render_all():
        print(f"  wrote {path.relative_to(path.parents[2])}")
