-- One row per employee per BUSINESS day of presence.
--
-- Grouped on business_date, not local_date: stg_badge_taps has already moved
-- pre-dawn taps back to the shift they belong to, so a night worker appears
-- once rather than as two half-days.
--
-- `tap_workplace` is the workplace of the first tap. Someone who visits two
-- buildings in a day is counted as present in the one they arrived at, and
-- `workplace_count` preserves the fact that they moved - which is the honest
-- way to keep the fact table at one row per employee-day without pretending
-- multi-building days do not happen.

select
    emp_id,
    business_date as local_date,
    min(tap_ts_local)                                           as first_tap_ts,
    max(tap_ts_local)                                           as last_tap_ts,
    min(tap_ts_utc)                                             as first_tap_ts_utc,
    max(tap_ts_utc)                                             as last_tap_ts_utc,
    count(*)                                                    as tap_count,
    count(distinct workplace_code)                              as workplace_count,
    arg_min(workplace_code, tap_ts_local)                       as tap_workplace,
    arg_min(city, tap_ts_local)                                 as tap_city,
    arg_min(region, tap_ts_local)                               as tap_region,
    arg_min(tower, tap_ts_local)                                as tower_first,
    arg_max(tower, tap_ts_local)                                as tower_last,
    arg_min(floor, tap_ts_local)                                as floor_first,
    arg_max(floor, tap_ts_local)                                as floor_last,
    date_diff('minute', min(tap_ts_local), max(tap_ts_local))   as span_minutes,
    bool_or(is_late_reported)                                   as has_late_reported_tap,
    bool_or(has_missing_timezone)                               as has_missing_timezone,
    bool_or(is_overnight_tap)                                   as has_overnight_tap
from {{ ref('stg_badge_taps') }}
group by emp_id, business_date
