"""Keep the registry and the warehouse from drifting apart.

The failure this prevents is mundane and common: someone adds a measure to a
mart, a dashboard starts using it, and six months later nobody can say who owns
it or what it means. The check runs in both directions -

  * every column a metric claims must exist in the warehouse, and
  * every measure column in a fact table must be claimed by some metric

- so an unregistered measure fails CI at the moment it is added, which is the
only moment when writing the definition is cheap.

It also checks that parameters shared between the simulator, dbt and the
registry hold the same value, because a buffer that means 10% in one place and
15% in another produces two defensible numbers and no way to choose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb
import yaml

from ..config import DBT_DIR, SEMANTIC_DIR, WAREHOUSE_PATH, Config, load_config
from .compile import MART_SCHEMA, MetricCompilationError, compile_metric
from .registry import Registry, load_registry

# Columns that are keys or dimensions, not measures. Anything else numeric in a
# fact table is a measure and needs an owner.
NON_MEASURE_COLUMNS = {
    "local_date",
    "iso_year_week",
    "workplace_code",
    "city",
    "region",
    "country",
    "dept_l1",
    "dept_l2",
    "dept_l3",
    "dept_l4",
    "tower",
    "floor",
    "local_hour",
    "is_weekend",
    "is_china_region",
    "required_days_per_week",
}

# Fact tables whose measures must be registered. Dimensions are excluded: a
# dimension attribute is not a metric and forcing one on it would be noise.
GOVERNED_MODELS = {"fct_attendance_daily", "fct_workplace_capacity_daily"}

WAIVER_PATH = SEMANTIC_DIR / "unregistered_measures.yml"

NUMERIC_TYPES = {
    "BIGINT",
    "INTEGER",
    "SMALLINT",
    "TINYINT",
    "HUGEINT",
    "DOUBLE",
    "FLOAT",
    "DECIMAL",
    "REAL",
}


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_metrics: int = 0
    governed_columns: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [
            f"Checked {self.checked_metrics} metrics against {self.governed_columns} "
            f"governed measure columns.",
        ]
        for warning in self.warnings:
            lines.append(f"  WARN  {warning}")
        for error in self.errors:
            lines.append(f"  FAIL  {error}")
        lines.append("  OK    registry and warehouse agree" if self.ok else "  FAILED")
        return "\n".join(lines)


def _warehouse_columns() -> dict[str, dict[str, str]]:
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        rows = con.execute(
            """
            select table_name, column_name, data_type
            from information_schema.columns
            where table_schema = ?
            """,
            [MART_SCHEMA],
        ).fetchall()
    finally:
        con.close()
    out: dict[str, dict[str, str]] = {}
    for table, column, dtype in rows:
        out.setdefault(table, {})[column] = dtype.upper()
    return out


def _dbt_vars() -> dict:
    project = yaml.safe_load((DBT_DIR / "dbt_project.yml").read_text())
    return project.get("vars", {}) or {}


def _load_waivers(report: ValidationReport) -> set[str]:
    """Measures explicitly declared not to be metrics, with a reason and owner."""
    if not WAIVER_PATH.exists():
        return set()
    raw = yaml.safe_load(WAIVER_PATH.read_text()) or {}
    waived: set[str] = set()
    for entry in raw.get("waivers", []) or []:
        columns = entry.get("columns") or []
        if not entry.get("reason", "").strip():
            report.errors.append(
                f"waiver for {', '.join(columns)} has no reason - a waiver without a "
                "justification is just a disabled check"
            )
            continue
        if not entry.get("owner", "").strip():
            report.errors.append(f"waiver for {', '.join(columns)} has no owner")
            continue
        waived.update(columns)
    return waived


def validate(cfg: Config | None = None, registry: Registry | None = None) -> ValidationReport:
    cfg = cfg or load_config()
    registry = registry or load_registry()
    report = ValidationReport(checked_metrics=len(registry))

    if not WAREHOUSE_PATH.exists():
        report.errors.append(
            f"no warehouse at {WAREHOUSE_PATH}; run `make dbt` before validating metrics"
        )
        return report

    tables = _warehouse_columns()
    claimed: set[str] = _load_waivers(report)
    waived = set(claimed)

    for metric in registry:
        # Direction 1: everything the metric references must exist.
        for model in metric.models:
            if model.startswith(("fct_", "dim_")) and model not in tables:
                report.errors.append(f"{metric.name}: model {model} is not in the warehouse")
        for reference in metric.source_columns:
            if "." not in reference:
                report.errors.append(
                    f"{metric.name}: source column {reference!r} must be written as model.column"
                )
                continue
            model, column = reference.split(".", 1)
            if model not in tables:
                report.errors.append(f"{metric.name}: unknown model {model} in {reference}")
            elif column not in tables[model]:
                report.errors.append(
                    f"{metric.name}: column {reference} does not exist in the warehouse"
                )
            else:
                claimed.add(reference)

        # Every metric must actually compile, at every grain it advertises.
        for grain in metric.time_grain:
            try:
                compile_metric(registry, metric.name, ["region"], grain, cfg=cfg)
            except MetricCompilationError as exc:
                report.errors.append(f"{metric.name}: does not compile at {grain} grain - {exc}")

        if metric.metric_type == "composite" and metric.source_columns:
            report.errors.append(
                f"{metric.name}: a composite must not claim source columns directly - it "
                "inherits them from its components"
            )

    # Direction 2: every governed measure must be claimed by some metric.
    for model in sorted(GOVERNED_MODELS):
        if model not in tables:
            report.errors.append(f"governed model {model} is missing from the warehouse")
            continue
        for column, dtype in sorted(tables[model].items()):
            if column in NON_MEASURE_COLUMNS or dtype not in NUMERIC_TYPES:
                continue
            report.governed_columns += 1
            if f"{model}.{column}" not in claimed:
                report.errors.append(
                    f"{model}.{column} is a measure with no metric definition. Either register a "
                    "metric for it or move it out of a governed fact table."
                )

    # A waiver for a column that no longer exists is stale and should be
    # removed, or it will quietly cover a future column with the same name.
    for reference in sorted(waived):
        model, _, column = reference.partition(".")
        if model not in tables or column not in tables[model]:
            report.errors.append(
                f"stale waiver: {reference} is no longer a column in the warehouse"
            )

    # Parameters shared between config, dbt and the registry.
    dbt_vars = _dbt_vars()
    for metric in registry:
        for parameter in metric.parameters:
            config_value = getattr(cfg.metrics, parameter, None)
            if config_value is None:
                report.errors.append(
                    f"{metric.name}: parameter {parameter!r} is not defined in conf/sim.yaml"
                )
                continue
            if parameter in dbt_vars and dbt_vars[parameter] != config_value:
                report.errors.append(
                    f"parameter {parameter!r} disagrees: conf/sim.yaml says {config_value}, "
                    f"dbt_project.yml says {dbt_vars[parameter]}"
                )

    # Soft checks: things that are not wrong, but are worth seeing.
    for metric in registry:
        if not metric.synonyms:
            report.warnings.append(f"{metric.name}: no synonyms, so catalogue search will miss it")
        if not metric.downstream_consumers:
            report.warnings.append(
                f"{metric.name}: no declared consumers, so impact analysis cannot see it"
            )
    return report


if __name__ == "__main__":  # pragma: no cover
    import sys

    report = validate()
    print(report.render())
    sys.exit(0 if report.ok else 1)
