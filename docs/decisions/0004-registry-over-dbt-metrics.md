# 4. A YAML metric registry, not dbt metrics alone

**Status:** accepted · **Date:** 2025-06-30

## Context

dbt already has a metrics spec. Adding a second definition layer needs to earn
its place, or it is just another thing to keep in sync.

## What dbt metrics do well

They live next to the models, they are versioned with the SQL, and they cannot
reference a column that does not exist. For a team whose consumers are all dbt
consumers, that is usually enough, and this ADR is not an argument that it is
not.

## What this platform needed on top

1. **Search by the word people use.** Three teams say desk, seat and
   workstation for the same thing. A catalogue that only answers to its own
   vocabulary gets opened once. Synonyms are a first-class field, and the
   catalogue search matches on them.
2. **Composition rules that are enforced, not documented.** A headcount is a
   stock and must average over days; a peak must take the maximum; a ratio must
   be recomputed from its components rather than averaged. `time_composition`
   makes this a field the compiler reads, so summing a headcount over five days
   is impossible rather than merely discouraged.
3. **Regional variants** (see ADR 3), substituted per row.
4. **Ownership and sensitivity** on every metric, so the catalogue can answer
   "who do I ask" and an access layer has something to enforce against.
5. **A two-way governance check.** A metric cannot claim a column that does not
   exist, *and* a measure in a governed fact table cannot exist without either
   a metric or a written waiver naming an owner and a reason. The second
   direction is the one dbt does not give you, and it is the one that stops the
   catalogue rotting: the moment an unregistered measure appears, CI fails,
   which is the only moment when writing the definition is cheap.

## Decision

One YAML file per metric under `semantic/metrics/`, validated by a Pydantic
schema, compiled by `semantic/compile.py` into SQL, a Markdown catalogue and a
lineage graph.

The schema refuses a business definition over 40 words, a ratio without a
denominator, a composite that restates its components' SQL instead of
referencing them, and a version that disagrees with its own changelog.

## Consequences

Two places now describe a number: the dbt model computes it, the registry
defines it. The validator exists precisely because that duplication is a risk,
and it is checked in CI and in a pre-commit hook.

Parameters shared between the simulator, dbt and the registry — the seat-demand
buffer, for one — are compared across all three. A buffer that means 10% in one
place and 15% in another produces two defensible numbers and no way to choose
between them.
