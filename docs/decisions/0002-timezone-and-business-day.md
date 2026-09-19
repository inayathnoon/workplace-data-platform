# 2. Timezones are resolved in staging, and a shift belongs to the day it started

**Status:** accepted · **Date:** 2025-06-30

## Context

Badge taps are stored in UTC. Attendance is a local-day concept. A night worker
who enters at 17:30 and leaves at 05:30 has worked one day, not two.

## Decision

Three rules, all in `stg_badge_taps` and nowhere else.

1. **Carry both timestamps.** `tap_ts_utc` and `tap_ts_local`, converted using
   the workplace's IANA timezone. Downstream models use local.
2. **A missing timezone falls back to UTC and raises a flag.** It does not
   guess a zone from the country. A wrong guess produces a confidently wrong
   local date; an explicit fallback plus `has_missing_timezone` produces a
   number somebody can choose to distrust. The DQ check reports how many desks
   are affected.
3. **An exit inherits the business day of the entry it follows**, and an entry
   before 04:00 local belongs to the previous day.

## Why the third rule is not just a clock cutover

A cutover alone was tried first. It fixed the common case and left a residue:
attendance ran about two points high in every region, because a shift ending at
05:30 fell after the cutover and opened a second business day for the same
person. Raising the cutover only moves the boundary somewhere else — there is
no hour that is both after every night shift ends and before every early start
begins.

Anchoring to the preceding entry has no such boundary. It holds for a shift of
any length, and the cutover survives only to handle the case it is actually
good at: an entry with no earlier entry to anchor to.

With the rule in place, the recovered attendance rate equals the simulator's
realised rate exactly, numerator and denominator, in all four regions.

## Consequences

A genuine 03:00 arrival is filed to the previous day. That is accepted: night
workers outnumber pre-dawn starters heavily, and the alternative counts one
shift twice. `is_overnight_tap` is kept on the row so the population affected
can be measured rather than assumed.
