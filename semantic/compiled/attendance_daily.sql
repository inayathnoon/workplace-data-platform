-- Generated from semantic/metrics/attendance_daily.yml. Do not edit.
-- The number of people who badged into a workplace on a given day, counted once each however many times they tapped.

with daily as (
    select
        workplace_code,
        local_date,
        iso_year_week as period,
        region,
        sum(attendance_actual) as attendance_daily
    from main_marts.fct_workplace_capacity_daily
    where (not is_weekend)
    group by workplace_code, local_date, iso_year_week, region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        avg(attendance_daily) as attendance_daily
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(attendance_daily) as attendance_daily
from per_workplace
group by period, region
order by period, region
