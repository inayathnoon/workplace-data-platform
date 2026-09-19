# 1. The central fact is one row per employee per local day

**Status:** accepted · **Date:** 2025-06-30

## Context

Five feeds arrive at five different grains: a daily HR snapshot per employee,
badge taps per swipe, leave per request, travel per trip, space per floor.
Every question the platform answers — attendance, compliance, occupancy,
vacancy — is some aggregate over "was this person in this building on this
day". Something has to be the place where those five grains are reconciled,
and whatever that is becomes the table everything else is built on.

## Options

**Tap grain.** Keep the fact at one row per swipe and aggregate on read. Loses
nothing, and answers questions about movement well. But it cannot represent an
absence: a person who did not come in has no row, and "no-show" is the single
most valuable state in this domain. Every absence question becomes an anti-join
against a population that has to be derived somewhere else anyway.

**Employee-week.** Smaller, and matches how policy is written ("three days a
week"). But it cannot answer which days, so peak-day sizing — the number that
actually drives desk counts — is not derivable from it.

**Employee-day.** One row per employee per workplace-local day, whether or not
they attended, carrying the resolved state and the evidence for it.

## Decision

Employee-day, keyed `(emp_id, local_date)`, materialised as
`int_employee_day`.

Local day, not UTC day: attendance is a local concept, and an 01:20 tap in
Ironwold and an 01:20 tap in Aurelia are different local days. The business day
is refined further by the rule in ADR 2.

## Consequences

The table is large — employees times days, about 21.6M rows on the full profile
— and that is the cost. It buys three things:

- Absence is a row, not the absence of a row, so every state is countable the
  same way.
- Precedence is resolved once, in one place, and the rule that fired is stored
  on the row. Two dashboards cannot disagree about whether a terminated
  employee on approved leave counts as absent.
- Both facts above it (`fct_attendance_daily` keyed on expected workplace,
  `fct_workplace_capacity_daily` keyed on actual) are aggregates of the same
  table, so they cannot drift apart.

The grain is asserted by a singular dbt test, a DQ check and a blocking Dagster
asset check. A duplicated employee-day inflates every downstream count while
leaving every number plausible, which is the worst kind of bug to find late.
