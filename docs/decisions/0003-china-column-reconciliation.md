# 3. Regional source differences are reconciled inside the metric, per row

**Status:** accepted · **Date:** 2025-06-30

## Context

The CN space system reports the free-sharing desk pool *inside*
`allocated_workstations`. Everywhere else the two columns are disjoint and
available capacity is their sum.

The column names are identical in both regions. Adding them everywhere
overstates CN capacity by about 8%, and nothing errors — the query runs, the
types match, the number is simply wrong. This is the failure mode a semantic
layer is for: not a broken pipeline, a plausible wrong answer.

## Options

**Fix it in the source.** Correct, and unavailable: the extract is owned by
another system.

**Two metrics, `available_workstations_cn` and `available_workstations_row`.**
Honest, and it pushes the problem onto every consumer. Whoever builds the
global roll-up has to know both exist and combine them correctly, which is the
same knowledge the layer was supposed to hold.

**One metric, reconciled behind the name.**

## Decision

One metric name. The registry declares the regional variant, and the compiler
substitutes it — **at row level, inside the aggregate**:

```sql
sum(case when region = 'CN'
         then allocated_workstations
         else allocated_workstations + free_sharing_workstations end)
```

Not a CASE wrapped around `sum(...)`. That form is only correct while `region`
happens to be in the GROUP BY; group by city, and one branch is silently chosen
for a group that may contain both semantics. Row-level substitution is correct
at every grain, which is the whole point of putting it in the compiler rather
than in a query.

`dim_workplace.available_workstations` applies the same rule, so a consumer who
bypasses the semantic layer still gets the right answer.

## Consequences

The registry's variant block is load-bearing and needs a test, not just a
comment. Three exist: one that the compiled SQL puts the CASE inside the
aggregate, one that CN's `available` equals `allocated` in the dimension, and
one that the generator still produces the quirk at all — if the simulator
stopped planting it, the reconciliation would be testing nothing.
