"""Streamlit front end: metric catalogue, lineage explorer, DQ scorecard.

Three tabs because there are three questions people actually arrive with:
what does this number mean, where does it come from, and can I trust it today.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import streamlit as st

from workplace_platform.config import IMG_DIR, WAREHOUSE_PATH, load_config
from workplace_platform.dq.runner import defect_recall, run_checks
from workplace_platform.semantic.compile import MetricCompilationError, compile_metric
from workplace_platform.semantic.registry import load_registry
from workplace_platform.semantic.validate import validate

st.set_page_config(page_title="Workplace data platform", page_icon="📐", layout="wide")

STATUS_ICON = {"pass": "🟢", "warn": "🟡", "fail": "🔴"}


@st.cache_resource
def _registry():
    return load_registry()


@st.cache_resource
def _config():
    return load_config()


@st.cache_data
def _run_metric(sql: str) -> pd.DataFrame:
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


@st.cache_data(ttl=300)
def _scorecard():
    card = run_checks()
    return (
        pd.DataFrame(
            [
                {
                    "status": r.status.value,
                    "check": r.name,
                    "category": r.category.value,
                    "value": r.value,
                    "explanation": r.explanation,
                    "expected": r.name in card.expected_failures,
                }
                for r in card.results
            ]
        ),
        pd.DataFrame(defect_recall(card)),
        card.counts,
        len(card.unexpected_failures),
    )


registry = _registry()
cfg = _config()

st.title("Workplace data platform")
st.caption(
    "All data is programmatically generated. No proprietary, confidential or personal data, "
    "and no real operational figures. "
    f"Profile **{cfg.profile_name}**, seed **{cfg.seed}**, window {cfg.start_date} to "
    f"{cfg.end_date}."
)

catalogue_tab, lineage_tab, quality_tab = st.tabs(["Metric catalogue", "Lineage", "Data quality"])


# --- Catalogue -------------------------------------------------------------

with catalogue_tab:
    left, right = st.columns([1, 2], gap="large")

    with left:
        term = st.text_input(
            "Search",
            placeholder="desk, seat, vacancy, rto…",
            help="Matches metric names, display names and synonyms. Searching for the word a "
            "team actually uses is the point of the synonym list.",
        )
        hits = registry.search(term)
        st.caption(f"{len(hits)} of {len(registry)} metrics")
        names = [m.name for m in hits]
        selected = st.radio("Metrics", names, label_visibility="collapsed") if names else None
        if not names:
            st.info(
                "No metric answers to that word. If it is a word people use, it belongs "
                "in a synonym list."
            )

    with right:
        if selected:
            metric = registry[selected]
            st.subheader(metric.display_name)
            st.write(" ".join(metric.definition_business.split()))

            cols = st.columns(4)
            cols[0].metric("Type", metric.metric_type)
            cols[1].metric("Owner", metric.owner)
            cols[2].metric("Sensitivity", metric.sensitivity_tier)
            cols[3].metric("Version", metric.version)

            st.caption(
                f"Grain **{metric.grain}** · composes over time by **{metric.time_composition}** "
                f"· time grains: {', '.join(metric.time_grain)}"
            )
            if metric.synonyms:
                st.caption("Also called: " + ", ".join(metric.synonyms))
            if metric.components:
                st.caption("Built from: " + ", ".join(metric.components))
            if metric.invalid_dimension_combinations:
                for rule in metric.invalid_dimension_combinations:
                    st.warning(rule, icon="⚠️")

            st.markdown("**Run it**")
            run_cols = st.columns([2, 1])
            dimensions = run_cols[0].multiselect(
                "Dimensions",
                metric.allowed_dimensions,
                default=["region"] if "region" in metric.allowed_dimensions else [],
            )
            grain = run_cols[1].selectbox("Time grain", [None, *metric.time_grain])

            try:
                compiled = compile_metric(registry, metric.name, dimensions, grain, cfg=cfg)
                st.code(compiled.sql, language="sql")
                if WAREHOUSE_PATH.exists():
                    st.dataframe(_run_metric(compiled.sql), use_container_width=True, height=280)
                else:
                    st.info("No warehouse yet. Run `make pipeline`.")
            except MetricCompilationError as exc:
                # A refusal is a feature: the compiler will not answer a
                # question the registry says is not answerable.
                st.error(f"Refused: {exc}", icon="⛔")

            with st.expander("Changelog"):
                for entry in metric.changelog:
                    st.write(
                        f"**v{entry.version}** ({entry.date}) — {' '.join(entry.change.split())}"
                    )


# --- Lineage ---------------------------------------------------------------

with lineage_tab:
    st.subheader("Where metrics come from")
    lineage_image = IMG_DIR / "metric_lineage.png"
    if lineage_image.exists():
        st.image(str(lineage_image), use_container_width=True)
    else:
        st.info("Run `make semantic` to generate the lineage graph.")

    st.markdown("**Upstream and downstream, per metric**")
    rows = []
    for metric in sorted(registry, key=lambda m: m.name):
        rows.append(
            {
                "metric": metric.name,
                "type": metric.metric_type,
                "models": ", ".join(sorted(metric.models)),
                "components": ", ".join(metric.components) or "—",
                "consumers": ", ".join(metric.downstream_consumers) or "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown("**Registry versus warehouse**")
    report = validate(cfg, registry)
    if report.ok:
        st.success(
            f"{report.checked_metrics} metrics agree with {report.governed_columns} governed "
            "measure columns.",
            icon="✅",
        )
    else:
        for error in report.errors:
            st.error(error, icon="⛔")
    for warning in report.warnings:
        st.caption(f"⚠️ {warning}")


# --- Data quality ----------------------------------------------------------

with quality_tab:
    if not WAREHOUSE_PATH.exists():
        st.info("No warehouse yet. Run `make pipeline`.")
    else:
        results, recall, counts, unexpected = _scorecard()

        cols = st.columns(4)
        cols[0].metric("Pass", counts["pass"])
        cols[1].metric("Warn", counts["warn"])
        cols[2].metric("Fail", counts["fail"])
        cols[3].metric("Unexpected", unexpected)

        if unexpected == 0:
            st.success(
                "Every failing check is a defect the simulator plants on purpose, and each one "
                "is recorded in conf/dq_expectations.yml with an owner and a review date.",
                icon="✅",
            )
        else:
            st.error(
                f"{unexpected} check(s) failed that nobody has written down a reason for.",
                icon="⛔",
            )

        if not recall.empty:
            st.markdown("**Did the checks find the planted defects?**")
            st.dataframe(recall, use_container_width=True, hide_index=True)

        st.markdown("**All checks**")
        category = st.multiselect(
            "Category",
            sorted(results["category"].unique()),
            default=sorted(results["category"].unique()),
        )
        view = results[results["category"].isin(category)].copy()
        view["status"] = view["status"].map(lambda s: f"{STATUS_ICON[s]} {s}")
        st.dataframe(view, use_container_width=True, hide_index=True)

        scorecard_image = IMG_DIR / "dq_scorecard.png"
        if scorecard_image.exists():
            st.image(str(scorecard_image), use_container_width=True)
