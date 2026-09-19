"""The metric registry: schema, loader, and validation.

A metric definition is a contract between a business owner and a SQL
expression. Keeping it in YAML with a strict schema, rather than in dbt models
alone, buys three things dbt metrics do not:

* Metrics carry ownership, synonyms and sensitivity, so the catalogue can be
  searched by the words people actually use and the access layer has something
  to enforce against.
* Composition rules are explicit. A ratio that must never be averaged across
  cities says so in a field, and the compiler refuses rather than silently
  returning a mean of means.
* Regional source differences are resolved behind one metric name, in one
  place, instead of being re-derived in every consuming query.

See docs/decisions/0004-registry-over-dbt-metrics.md for the trade-off.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import METRICS_DIR

MetricType = Literal["atomic", "derived", "composite"]
Aggregation = Literal["sum", "avg", "ratio", "weighted_ratio", "max", "min"]
TimeComposition = Literal["sum", "avg", "max", "min", "last"]
SensitivityTier = Literal["L1", "L2", "L3", "L4"]
TimeGrain = Literal["day", "week", "month", "quarter"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Expression(_Base):
    """A SQL aggregate over one mart, optionally region-dependent.

    ``regional_variants`` is the mechanism the whole layer exists for: where a
    region's source columns mean something different, the difference is written
    down once here and compiled into a CASE, rather than being remembered (or
    forgotten) by each analyst.

    The variant is substituted at ROW level, into the ``{variant_term}``
    placeholder inside the aggregate - not wrapped around it. Wrapping a CASE
    around ``sum(...)`` would only be valid while the region happens to be in
    the GROUP BY, and would silently pick one branch for a mixed-region group.
    """

    model: str
    sql: str
    variant_term: str | None = None
    variant_default: str | None = None
    regional_variants: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_variants(self) -> Expression:
        if self.regional_variants and not (self.variant_term and self.variant_default):
            raise ValueError(
                "regional_variants needs a variant_term placeholder and a variant_default"
            )
        if self.variant_term and f"{{{self.variant_term}}}" not in self.sql:
            raise ValueError(f"sql does not contain the placeholder {{{self.variant_term}}}")
        return self

    def compile(self, region_column: str = "region") -> str:
        if not self.regional_variants:
            return self.sql
        cases = " ".join(
            f"when {region_column} = '{region}' then {expr}"
            for region, expr in sorted(self.regional_variants.items())
        )
        resolved = f"case {cases} else {self.variant_default} end"
        return self.sql.format(**{self.variant_term: resolved})

    @property
    def is_region_dependent(self) -> bool:
        return bool(self.regional_variants)


class ChangelogEntry(_Base):
    version: int
    date: str
    change: str


class MetricSpec(_Base):
    name: str
    display_name: str
    owner: str
    metric_type: MetricType

    definition_business: str
    definition_technical: str

    grain: str
    entity: str
    measure: str
    time_grain: list[TimeGrain]

    aggregation: Aggregation
    time_composition: TimeComposition

    allowed_dimensions: list[str]
    invalid_dimension_combinations: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)

    numerator: Expression | None = None
    denominator: Expression | None = None
    expression: Expression | None = None
    components: list[str] = Field(default_factory=list)
    # Arithmetic over component metric names, e.g.
    # "(peak_day_attendance / headcount_incumbent) * (1 + {seat_demand_buffer})".
    # Braced names are parameters resolved from conf/sim.yaml, so a buffer
    # change moves the metric and the simulator together or fails the
    # registry-versus-config check.
    composite_formula: str | None = None
    parameters: list[str] = Field(default_factory=list)

    sensitivity_tier: SensitivityTier
    synonyms: list[str] = Field(default_factory=list)
    source_columns: list[str] = Field(default_factory=list)
    upstream_models: list[str]
    downstream_consumers: list[str] = Field(default_factory=list)

    version: int
    changelog: list[ChangelogEntry]

    @model_validator(mode="after")
    def _check_shape(self) -> MetricSpec:
        if len(self.definition_business.split()) > 40:
            raise ValueError(
                f"{self.name}: definition_business must be under 40 words - if it needs more "
                "than that, the metric is doing two jobs and should be split"
            )
        # A composite expresses its ratio through composite_formula, so the
        # numerator/denominator requirement applies only to leaf metrics.
        ratio_like = (
            self.aggregation in ("ratio", "weighted_ratio") and self.metric_type != "composite"
        )
        if ratio_like and not (self.numerator and self.denominator):
            raise ValueError(f"{self.name}: a ratio metric needs a numerator and a denominator")
        if not ratio_like and self.expression is None and self.metric_type != "composite":
            raise ValueError(f"{self.name}: needs an expression")
        if self.metric_type == "composite":
            if not self.components:
                raise ValueError(
                    f"{self.name}: a composite metric must reference its components, not restate "
                    "their SQL - that is what makes it a composite"
                )
            if not self.composite_formula:
                raise ValueError(f"{self.name}: a composite metric needs a composite_formula")
            for component in self.components:
                if component not in self.composite_formula:
                    raise ValueError(
                        f"{self.name}: component {component!r} is declared but never used in "
                        "composite_formula"
                    )
        if self.composite_formula and self.metric_type != "composite":
            raise ValueError(f"{self.name}: only composite metrics may declare a formula")
        if self.metric_type != "composite" and self.components:
            raise ValueError(f"{self.name}: only composite metrics may declare components")
        if self.version != max(c.version for c in self.changelog):
            raise ValueError(f"{self.name}: version must match the latest changelog entry")
        return self

    @property
    def models(self) -> set[str]:
        out = set(self.upstream_models)
        for expr in (self.expression, self.numerator, self.denominator):
            if expr is not None:
                out.add(expr.model)
        return out


class Registry(_Base):
    metrics: dict[str, MetricSpec]

    def __iter__(self):  # type: ignore[override]
        return iter(self.metrics.values())

    def __len__(self) -> int:
        return len(self.metrics)

    def __getitem__(self, name: str) -> MetricSpec:
        return self.metrics[name]

    def search(self, term: str) -> list[MetricSpec]:
        """Match on name, display name or synonym.

        Synonyms are not decoration. "Desk", "seat" and "workstation" are the
        same thing to three different teams, and a catalogue that only answers
        to its own vocabulary gets used once.
        """
        needle = term.strip().lower()
        if not needle:
            return sorted(self.metrics.values(), key=lambda m: m.name)
        hits = []
        for metric in self.metrics.values():
            haystack = [metric.name, metric.display_name, *metric.synonyms]
            if any(needle in h.lower() for h in haystack):
                hits.append(metric)
        return sorted(hits, key=lambda m: m.name)

    def resolve_components(self, name: str) -> list[MetricSpec]:
        return [self.metrics[c] for c in self.metrics[name].components]


def load_registry(path: Path | None = None) -> Registry:
    """Load every ``*.yml`` under ``semantic/metrics/`` as one metric each."""
    path = path or METRICS_DIR
    metrics: dict[str, MetricSpec] = {}
    for file in sorted(path.glob("*.yml")):
        raw = yaml.safe_load(file.read_text())
        spec = MetricSpec(**raw)
        if spec.name != file.stem:
            raise ValueError(f"{file.name}: metric name {spec.name!r} must match the filename")
        if spec.name in metrics:
            raise ValueError(f"duplicate metric {spec.name!r}")
        metrics[spec.name] = spec

    registry = Registry(metrics=metrics)
    _check_composites(registry)
    return registry


def _check_composites(registry: Registry) -> None:
    for metric in registry.metrics.values():
        for component in metric.components:
            if component not in registry.metrics:
                raise ValueError(
                    f"{metric.name}: component {component!r} is not a registered metric"
                )
            if registry.metrics[component].name == metric.name:
                raise ValueError(f"{metric.name}: a composite cannot reference itself")
