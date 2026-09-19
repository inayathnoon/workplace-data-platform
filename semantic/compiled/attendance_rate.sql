-- Generated from semantic/metrics/attendance_rate.yml. Do not edit.
-- Of the days people were expected in the office and able to be, the share on which they actually came in. Approved leave and travel are excluded from both sides.

with daily as (
    select
        workplace_code,
        local_date,
        iso_year_week as period,
        region,
        sum(attendance_expected_met) as attendance_rate_numerator,
        sum(attendance_expected_days) as attendance_rate_denominator
    from main_marts.fct_attendance_daily
    group by workplace_code, local_date, iso_year_week, region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        avg(attendance_rate_numerator) as attendance_rate_numerator,
        avg(attendance_rate_denominator) as attendance_rate_denominator
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(attendance_rate_numerator) / nullif(sum(attendance_rate_denominator), 0) as attendance_rate,
    sum(attendance_rate_numerator) as attendance_rate_numerator,
    sum(attendance_rate_denominator) as attendance_rate_denominator
from per_workplace
group by period, region
order by period, region
