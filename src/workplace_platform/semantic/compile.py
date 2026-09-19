"""Compile the metric registry into SQL, a catalogue and a lineage graph.

The compiler is deterministic and has no model of the question being asked: it
takes a metric name, a set of dimensions and a time grain, and emits SQL. If the
request breaks a rule the registry declares - an invalid dimension, a ratio
being averaged, a time grain the metric does not support - it raises rather than
returning a number that would look fine on a chart.

That refusal is the whole product. Anyone can compute an attendance rate; the
value is in making it impossible to compute a *different* attendance rate by
accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import DOCS_DIR, IMG_DIR, Config, load_config
from .registry import MetricSpec, Registry, load_registry

MART_SCHEMA = "main_marts"

# How each declared time grain is expressed in each mart.
TIME_COLUMNS = {
    "day": "local_date",
    "week": "iso_year_week",
    "month": "strftime(local_date, '%Y-%m')",
}


class MetricCompilationError(ValueError):
    """Raised when a request breaks a rule the registry declares."""


@dataclass(frozen=True)
class CompiledMetric:
    name: str
    sql: str
    dimensions: tuple[str, ...]
    time_grain: str | None
    models: tuple[str, ...]


def _qualified(model: str) -> str:
    return f"{MART_SCHEMA}.{model}"


def _time_expression(time_grain: str) -> str:
    if time_grain not in TIME_COLUMNS:
        raise MetricCompilationError(f"unsupported time grain {time_grain!r}")
    return TIME_COLUMNS[time_grain]


def _validate_request(
    metric: MetricSpec,
    dimensions: list[str],
    time_grain: str | None,
) -> None:
    if time_grain is not None and time_grain not in metric.time_grain:
        raise MetricCompilationError(
            f"{metric.name} is not defined at {time_grain} grain; it supports "
            f"{', '.join(metric.time_grain)}"
        )
    unknown = [d for d in dimensions if d not in metric.allowed_dimensions]
    if unknown:
        raise MetricCompilationError(
            f"{metric.name} cannot be broken down by {', '.join(unknown)}; allowed dimensions "
            f"are {', '.join(metric.allowed_dimensions)}"
        )
    # A region-dependent metric sliced without region still compiles: the CASE
    # is evaluated per row, before aggregation, so the answer stays correct.
    # This is the difference the row-level substitution buys.


ENTITY_KEY = "workplace_code"


def _time_expression_for(time_grain: str | None) -> str | None:
    return None if time_grain is None else _time_expression(time_grain)


def _compose_across_time(op: str, column: str) -> str:
    """How a measure combines across the days inside one period.

    This is the field most metric layers leave implicit, and it is where the
    wrong answers come from. Headcount averages over days; a peak takes the
    maximum; a count of events sums. Summing a headcount over five days gives
    a number five times too large that still looks plausible on a chart.
    """
    if op not in ("sum", "avg", "max", "min", "last"):
        raise MetricCompilationError(f"unknown time composition {op!r}")
    sql_op = {"last": "last", "avg": "avg"}.get(op, op)
    return f"{sql_op}({column})"


def compile_metric(
    registry: Registry,
    name: str,
    dimensions: list[str] | None = None,
    time_grain: str | None = None,
    extra_filters: list[str] | None = None,
    cfg: Config | None = None,
) -> CompiledMetric:
    """Compile one metric into a single self-contained SELECT.

    Three levels, because a metric has two different kinds of aggregation and
    collapsing them into one GROUP BY silently mixes them up:

      L1  per workplace-day   the metric's own expression
      L2  per workplace-period  composed across days by time_composition
      L3  per requested dims    composed across workplaces by addition, with
                                ratios recomputed from their components

    The demo of why: seat demand is a weekly peak over an average headcount.
    Done in one pass it divides one workplace's busiest day by five days of
    everybody's headcount, and reports a sharing ratio near zero.
    """
    if name not in registry.metrics:
        raise MetricCompilationError(
            f"unknown metric {name!r}. A metric that is not in the registry does not exist: "
            "add a definition before writing SQL against it."
        )
    metric = registry[name]
    dimensions = list(dimensions or [])
    cfg = cfg or load_config()
    _validate_request(metric, dimensions, time_grain)

    if metric.metric_type == "composite":
        return _compile_composite(registry, metric, dimensions, time_grain, extra_filters, cfg)

    is_ratio = metric.aggregation in ("ratio", "weighted_ratio")
    if is_ratio:
        assert metric.numerator and metric.denominator
        if metric.numerator.model != metric.denominator.model:
            raise MetricCompilationError(
                f"{metric.name}: numerator and denominator must come from the same mart"
            )
        model = metric.numerator.model
        terms = {
            f"{metric.name}_numerator": metric.numerator.compile(),
            f"{metric.name}_denominator": metric.denominator.compile(),
        }
    else:
        assert metric.expression
        model = metric.expression.model
        terms = {metric.name: metric.expression.compile()}

    period_expr = _time_expression_for(time_grain)
    filters = list(metric.filters) + list(extra_filters or [])

    # L1: the metric's own expression, at the mart's own grain.
    l1_keys = [ENTITY_KEY, "local_date", *dimensions]
    if period_expr:
        l1_keys.insert(2, f"{period_expr} as period")
    l1_group = [ENTITY_KEY, "local_date", *dimensions]
    if period_expr:
        l1_group.insert(2, period_expr)
    l1 = [
        "select",
        "    " + ",\n    ".join([*l1_keys, *(f"{sql} as {alias}" for alias, sql in terms.items())]),
        f"from {_qualified(model)}",
    ]
    if filters:
        l1.append("where " + "\n  and ".join(f"({f})" for f in filters))
    l1.append("group by " + ", ".join(l1_group))

    # L2: compose across the days inside each period, per workplace.
    l2_keys = [ENTITY_KEY, *(["period"] if period_expr else []), *dimensions]
    l2 = [
        "select",
        "    "
        + ",\n    ".join(
            [
                *l2_keys,
                *(
                    f"{_compose_across_time(metric.time_composition, alias)} as {alias}"
                    for alias in terms
                ),
            ]
        ),
        "from daily",
        "group by " + ", ".join(l2_keys),
    ]

    # L3: compose across workplaces. Additive measures sum; ratios are
    # recomputed from summed components, never averaged.
    l3_keys = [*(["period"] if period_expr else []), *dimensions]
    if is_ratio:
        num, den = f"{metric.name}_numerator", f"{metric.name}_denominator"
        measures = [
            f"sum({num}) / nullif(sum({den}), 0) as {metric.name}",
            f"sum({num}) as {num}",
            f"sum({den}) as {den}",
        ]
    else:
        measures = [f"sum({metric.name}) as {metric.name}"]

    l3 = ["select"]
    l3.append("    " + ",\n    ".join([*l3_keys, *measures]))
    l3.append("from per_workplace")
    if l3_keys:
        l3.append("group by " + ", ".join(l3_keys))
        l3.append("order by " + ", ".join(l3_keys))

    sql = (
        "with daily as (\n"
        + _indent("\n".join(l1))
        + "\n),\n\nper_workplace as (\n"
        + _indent("\n".join(l2))
        + "\n)\n\n"
        + "\n".join(l3)
    )
    return CompiledMetric(
        name=metric.name,
        sql=sql,
        dimensions=tuple(dimensions),
        time_grain=time_grain,
        models=(model,),
    )


def _compile_composite(
    registry: Registry,
    metric: MetricSpec,
    dimensions: list[str],
    time_grain: str | None,
    extra_filters: list[str] | None,
    cfg: Config,
) -> CompiledMetric:
    """Compile a composite by joining its components, never by restating them.

    Each component is compiled by the same code path a direct request would
    use, so a composite can never drift from the metrics it is built on. The
    join keys are exactly the requested dimensions.
    """
    parts: dict[str, CompiledMetric] = {}
    for component in metric.components:
        spec = registry[component]
        # Components are asked for at the composite's grain, which they must
        # support - the registry check here is what catches a composite built
        # on a metric that cannot legally be aggregated that way.
        component_grain = time_grain if time_grain in spec.time_grain else None
        if time_grain and component_grain is None:
            raise MetricCompilationError(
                f"{metric.name} needs {component} at {time_grain} grain, but {component} is "
                f"only defined at {', '.join(spec.time_grain)}"
            )
        component_dims = [d for d in dimensions if d in spec.allowed_dimensions]
        if component_dims != dimensions:
            missing = set(dimensions) - set(component_dims)
            raise MetricCompilationError(
                f"{metric.name} cannot be sliced by {', '.join(sorted(missing))}: component "
                f"{component} is not defined by that dimension"
            )
        parts[component] = compile_metric(
            registry, component, dimensions, time_grain, extra_filters, cfg
        )

    params = {p: getattr(cfg.metrics, p) for p in metric.parameters}
    formula = metric.composite_formula or ""
    for param, value in params.items():
        formula = formula.replace(f"{{{param}}}", repr(value))
    for component in metric.components:
        formula = formula.replace(component, f"c_{component}.{component}")

    keys = (["period"] if time_grain else []) + list(dimensions)
    ctes = ",\n".join(f"c_{name} as (\n{_indent(part.sql)}\n)" for name, part in parts.items())
    first, *rest = metric.components
    join_sql = "\n".join(
        f"inner join c_{other} on " + " and ".join(f"c_{first}.{k} = c_{other}.{k}" for k in keys)
        if keys
        else f"cross join c_{other}"
        for other in rest
    )
    select_keys = ", ".join(f"c_{first}.{k}" for k in keys)
    projection = f"{select_keys + ', ' if keys else ''}{formula} as {metric.name}"

    sql = (f"with {ctes}\nselect {projection}\nfrom c_{first}\n{join_sql}").rstrip()
    if keys:
        sql += "\norder by " + ", ".join(f"c_{first}.{k}" for k in keys)

    models = tuple(sorted({m for part in parts.values() for m in part.models}))
    return CompiledMetric(metric.name, sql, tuple(dimensions), time_grain, models)


def _indent(text: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


# --- Catalogue -------------------------------------------------------------


def render_catalogue(registry: Registry, cfg: Config) -> str:
    lines: list[str] = [
        "# Metric catalogue",
        "",
        "> Generated by `semantic/compile.py` from `semantic/metrics/*.yml`.",
        "> Do not edit by hand - edit the metric definition and recompile.",
        "",
        f"{len(registry)} metrics. "
        f"{sum(1 for m in registry if m.metric_type == 'atomic')} atomic, "
        f"{sum(1 for m in registry if m.metric_type == 'derived')} derived, "
        f"{sum(1 for m in registry if m.metric_type == 'composite')} composite.",
        "",
        "| Metric | Type | Owner | Tier | Business definition |",
        "| --- | --- | --- | --- | --- |",
    ]
    for metric in sorted(registry, key=lambda m: m.name):
        definition = " ".join(metric.definition_business.split())
        lines.append(
            f"| [`{metric.name}`](#{metric.name.replace('_', '-')}) | {metric.metric_type} "
            f"| {metric.owner} | {metric.sensitivity_tier} | {definition} |"
        )
    lines.append("")

    for metric in sorted(registry, key=lambda m: m.name):
        lines.extend(_render_metric(registry, metric, cfg))
    return "\n".join(lines) + "\n"


def _render_metric(registry: Registry, metric: MetricSpec, cfg: Config) -> list[str]:
    out = [
        f"## {metric.name}",
        "",
        f"**{metric.display_name}** - {' '.join(metric.definition_business.split())}",
        "",
        f"- **Type**: {metric.metric_type} ({metric.aggregation}, "
        f"composes over time by `{metric.time_composition}`)",
        f"- **Grain**: {metric.grain}, entity `{metric.entity}`",
        f"- **Time grains**: {', '.join(metric.time_grain)}",
        f"- **Owner**: {metric.owner} - **Sensitivity**: {metric.sensitivity_tier}",
        f"- **Version**: {metric.version}",
        f"- **Synonyms**: {', '.join(metric.synonyms) or 'none'}",
        f"- **Dimensions**: {', '.join(metric.allowed_dimensions)}",
    ]
    if metric.filters:
        out.append(f"- **Default filters**: {', '.join(f'`{f}`' for f in metric.filters)}")
    if metric.components:
        out.append(f"- **Components**: {', '.join(f'`{c}`' for c in metric.components)}")
    if metric.invalid_dimension_combinations:
        out.append("- **Not valid**:")
        out.extend(f"  - {rule}" for rule in metric.invalid_dimension_combinations)

    region_dependent = any(
        e is not None and e.is_region_dependent
        for e in (metric.expression, metric.numerator, metric.denominator)
    )
    if region_dependent:
        out.append(
            "- **Regional reconciliation**: source columns differ by region; the variant is "
            "substituted per row inside the aggregate."
        )

    out += ["", "Compiled SQL (by region, weekly):", "", "```sql"]
    grain = "week" if "week" in metric.time_grain else metric.time_grain[0]
    try:
        compiled = compile_metric(registry, metric.name, ["region"], grain, cfg=cfg)
        out.append(compiled.sql)
    except MetricCompilationError as exc:  # pragma: no cover - documented refusal
        out.append(f"-- refused: {exc}")
    out += ["```", "", "Changelog:", ""]
    out += [f"- v{c.version} ({c.date}): {' '.join(c.change.split())}" for c in metric.changelog]
    out += [""]
    return out


# --- Lineage ---------------------------------------------------------------


def render_lineage(registry: Registry, path: Path) -> Path:
    """Draw metric lineage as a layered graph: models, then metrics by type.

    Hand-laid rather than force-directed. A lineage picture is read left to
    right by someone asking "what feeds this", and a spring layout optimises
    for looking organic instead of for that question.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    layers: list[tuple[str, list[str]]] = [
        ("Marts", sorted({m for metric in registry for m in metric.models if m.startswith("fct")})),
        ("Atomic", sorted(m.name for m in registry if m.metric_type == "atomic")),
        ("Derived", sorted(m.name for m in registry if m.metric_type == "derived")),
        ("Composite", sorted(m.name for m in registry if m.metric_type == "composite")),
    ]
    positions: dict[str, tuple[float, float]] = {}
    for depth, (_, nodes) in enumerate(layers):
        for row, node in enumerate(nodes):
            offset = (max(len(n) for _, n in layers) - len(nodes)) / 2.0
            positions[node] = (depth * 3.0, -(row + offset))

    fig, ax = plt.subplots(figsize=(15, 8))
    for metric in registry:
        for model in metric.models:
            if model in positions and metric.name in positions:
                _arrow(ax, positions[model], positions[metric.name], "#b8c4d0")
        for component in metric.components:
            if component in positions:
                _arrow(ax, positions[component], positions[metric.name], "#7a8ca0")

    colours = {
        "Marts": "#dfe7ef",
        "Atomic": "#cfe3d4",
        "Derived": "#dcd6ef",
        "Composite": "#f1dfc9",
    }
    for layer_name, nodes in layers:
        for node in nodes:
            x, y = positions[node]
            ax.text(
                x,
                y,
                node.replace("_", "\n", 1),
                ha="center",
                va="center",
                fontsize=7.5,
                bbox={
                    "boxstyle": "round,pad=0.45",
                    "facecolor": colours[layer_name],
                    "edgecolor": "#8a97a6",
                    "linewidth": 0.7,
                },
            )
    for depth, (layer_name, _) in enumerate(layers):
        ax.text(
            depth * 3.0,
            1.2,
            layer_name,
            ha="center",
            fontsize=11,
            fontweight="bold",
            color="#33404f",
        )

    ax.set_xlim(-1.8, (len(layers) - 1) * 3.0 + 1.8)
    ax.set_ylim(-max(len(n) for _, n in layers) - 0.5, 2.0)
    ax.axis("off")
    ax.set_title("Metric lineage: marts to composite metrics", fontsize=13, pad=16)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
    return path


def _arrow(ax, start: tuple[float, float], end: tuple[float, float], colour: str) -> None:
    ax.annotate(
        "",
        xy=(end[0] - 0.95, end[1]),
        xytext=(start[0] + 0.95, start[1]),
        arrowprops={
            "arrowstyle": "->",
            "color": colour,
            "linewidth": 0.9,
            "connectionstyle": "arc3,rad=0.08",
        },
    )


def compile_all(cfg: Config | None = None) -> dict:
    cfg = cfg or load_config()
    registry = load_registry()

    catalogue_path = DOCS_DIR / "metrics.md"
    catalogue_path.parent.mkdir(parents=True, exist_ok=True)
    catalogue_path.write_text(render_catalogue(registry, cfg))

    sql_dir = Path(__file__).resolve().parents[3] / "semantic" / "compiled"
    sql_dir.mkdir(parents=True, exist_ok=True)
    for existing in sql_dir.glob("*.sql"):
        existing.unlink()
    for metric in registry:
        grain = "week" if "week" in metric.time_grain else metric.time_grain[0]
        compiled = compile_metric(registry, metric.name, ["region"], grain, cfg=cfg)
        (sql_dir / f"{metric.name}.sql").write_text(
            f"-- Generated from semantic/metrics/{metric.name}.yml. Do not edit.\n"
            f"-- {' '.join(metric.definition_business.split())}\n\n{compiled.sql}\n"
        )

    lineage_path = render_lineage(registry, IMG_DIR / "metric_lineage.png")
    return {
        "metrics": len(registry),
        "catalogue": catalogue_path,
        "sql_dir": sql_dir,
        "lineage": lineage_path,
    }


if __name__ == "__main__":  # pragma: no cover
    result = compile_all()
    print(f"compiled {result['metrics']} metrics")
    print(f"  catalogue -> {result['catalogue']}")
    print(f"  sql       -> {result['sql_dir']}")
    print(f"  lineage   -> {result['lineage']}")
