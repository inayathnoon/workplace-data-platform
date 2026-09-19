-- Badge taps in both UTC and workplace-local time, de-duplicated, and assigned
-- to a business day.
--
-- Three things happen here and nowhere else.
--
-- 1. Local time. Attendance is a local-day concept: a 23:50 tap and a 00:10 tap
--    are the same working day for the person and different days in UTC. We
--    carry both timestamps and derive everything from the local one.
--
-- 2. De-duplication on a time window, not on equality. A reader that emits the
--    same swipe twice sets a slightly different timestamp, so `distinct` does
--    nothing. We drop any tap within {{ var('tap_dedupe_window_seconds') }}
--    seconds of the previous tap by the same person, on the same device, in the
--    same direction - which is a re-swipe, not a second visit.
--
-- 3. The business day. A night worker who enters at 17:30 and leaves at 05:30
--    has worked one day, not two, and counting them twice inflates attendance
--    by the size of the night-shift population.
--
--    The rule: an exit inherits the business day of the entry it follows, and
--    an entry before {{ var('business_day_cutover_hour') }}:00 local belongs to
--    the previous day. Anchoring to the preceding entry rather than to a clock
--    cutover alone is what makes it hold for shifts of any length - a cutover
--    at 04:00 still misfiles a shift that ends at 05:30, and raising the
--    cutover until it does not just moves the problem to a later hour.
--
--    An exit with no preceding entry (the reader missed the swipe, or the
--    window starts mid-shift) falls back to the cutover rule.

with joined as (

    select
        t.emp_id,
        t.workplace_code,
        t.tower,
        t.floor,
        t.direction,
        t.device_id,
        cast(t.ingested_date as date)                               as ingested_date,
        t.tap_ts_utc,
        timezone(w.timezone, timezone('UTC', t.tap_ts_utc))         as tap_ts_local,
        w.timezone,
        w.city,
        w.region,
        w.has_missing_timezone
    from {{ source('raw', 'badge_taps') }} t
    inner join {{ ref('stg_workplace') }} w
        on t.workplace_code = w.workplace_code

),

deduplicated as (

    select *
    from (
        select
            *,
            lag(tap_ts_utc) over (
                partition by emp_id, device_id, direction
                order by tap_ts_utc
            ) as prev_tap_ts_utc
        from joined
    )
    where prev_tap_ts_utc is null
       or date_diff('second', prev_tap_ts_utc, tap_ts_utc) > {{ var('tap_dedupe_window_seconds') }}

),

with_cutover as (

    select
        *,
        extract(hour from tap_ts_local) < {{ var('business_day_cutover_hour') }}
            as is_overnight_tap,
        case
            when extract(hour from tap_ts_local) < {{ var('business_day_cutover_hour') }}
            then cast(tap_ts_local as date) - interval '1 day'
            else cast(tap_ts_local as date)
        end as cutover_date
    from deduplicated

),

anchored as (

    select
        *,
        last_value(
            case when direction = 'in' then cutover_date end ignore nulls
        ) over (
            partition by emp_id
            order by tap_ts_local
            rows between unbounded preceding and current row
        ) as entry_anchor_date
    from with_cutover

)

select
    emp_id,
    workplace_code,
    tower,
    floor,
    direction,
    device_id,
    tap_ts_utc,
    tap_ts_local,
    cast(tap_ts_local as date)                  as local_date,
    cast(coalesce(entry_anchor_date, cutover_date) as date) as business_date,
    extract(hour from tap_ts_local)             as local_hour,
    ingested_date,
    timezone,
    city,
    region,
    has_missing_timezone,
    is_overnight_tap,
    ingested_date > cast(tap_ts_local as date)  as is_late_reported
from anchored
