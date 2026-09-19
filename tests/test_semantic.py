"""The registry's rules, and the compiler's refusals.

The refusals matter more than the successes here: anyone can compile SQL, and
the value of the layer is that it will not compile a question the registry says
is unanswerable.
"""

from __future__ import annotations

import pytest
import yaml

from workplace_platform.config import METRICS_DIR
from workplace_platform.semantic.compile import MetricCompilationError, compile_metric
from workplace_platform.semantic.registry import MetricSpec, load_registry


def _spec(**overrides) -> dict:
    raw = yaml.safe_load((METRICS_DIR / "attendance_rate.yml").read_text())
    raw.update(overrides)
    return raw


def test_registry_loads_every_file():
    registry = load_registry()
    files = list(METRICS_DIR.glob("*.yml"))
    assert len(registry) == len(files) >= 14


def test_search_matches_synonyms(registry):
    assert "workstation_waste" in {m.name for m in registry.search("empty desks")}
    assert "attendance_rate" in {m.name for m in registry.search("rto")}


def test_verbose_business_definition_is_rejected():
    with pytest.raises(ValueError, match="under 40 words"):
        MetricSpec(**_spec(definition_business=" ".join(["word"] * 41)))


def test_ratio_without_denominator_is_rejected():
    raw = _spec()
    raw.pop("denominator")
    with pytest.raises(ValueError, match="numerator and a denominator"):
        MetricSpec(**raw)


def test_version_must_match_the_changelog():
    with pytest.raises(ValueError, match="version must match"):
        MetricSpec(**_spec(version=99))


def test_composite_must_reference_its_components(registry):
    spec = registry["seat_demand_rate"]
    assert spec.components
    for component in spec.components:
        assert component in spec.composite_formula
    # The whole point: no restated SQL.
    assert spec.expression is None


def test_unknown_metric_is_refused(registry):
    with pytest.raises(MetricCompilationError, match="unknown metric"):
        compile_metric(registry, "vibes", [], "day")


def test_illegal_dimension_is_refused(registry):
    with pytest.raises(MetricCompilationError, match="cannot be broken down by"):
        compile_metric(registry, "attendance_rate", ["badge_type"], "day")


def test_unsupported_time_grain_is_refused(registry):
    with pytest.raises(MetricCompilationError, match="not defined at day grain"):
        compile_metric(registry, "seat_demand_rate", [], "day")


def test_composite_refuses_a_dimension_it_does_not_declare(registry):
    """Checked against the composite's own allowed_dimensions first, so the
    refusal names the metric the caller asked for rather than an internal
    component they have never heard of."""
    with pytest.raises(MetricCompilationError, match="cannot be broken down by dept_l1"):
        compile_metric(registry, "seat_demand_rate", ["dept_l1"], "week")


def test_regional_variant_is_substituted_inside_the_aggregate(registry):
    """Row-level, not wrapped around sum(). Wrapping would break the moment the
    query groups by anything other than region."""
    sql = compile_metric(registry, "workstation_waste", ["city"], "week").sql
    assert "sum(case when region = 'CN'" in sql
    assert "case when region = 'CN' then sum(" not in sql


def test_headcount_averages_over_days_rather_than_summing(registry):
    """A stock summed over five days is five times too large and still looks
    plausible."""
    sql = compile_metric(registry, "headcount_incumbent", ["region"], "week").sql
    assert "avg(headcount_incumbent)" in sql


def test_peak_takes_the_max_over_days(registry):
    sql = compile_metric(registry, "peak_day_attendance", ["region"], "week").sql
    assert "max(peak_day_attendance)" in sql


def test_every_metric_compiles_at_every_declared_grain(registry, cfg):
    for metric in registry:
        for grain in metric.time_grain:
            compile_metric(registry, metric.name, ["region"], grain, cfg=cfg)
