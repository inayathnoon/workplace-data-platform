-- Generated from semantic/metrics/peak_day_attendance.yml. Do not edit.
-- The busiest single day in the period, per workplace. Desks have to exist on the busy day, so this is what capacity is sized against.

with daily as (
    select
        workplace_code,
        local_date,
        iso_year_week as period,
        region,
        max(attendance_actual) as peak_day_attendance
    from main_marts.fct_workplace_capacity_daily
    where (not is_weekend)
    group by workplace_code, local_date, iso_year_week, region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        max(peak_day_attendance) as peak_day_attendance
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(peak_day_attendance) as peak_day_attendance
from per_workplace
group by period, region
order by period, region
