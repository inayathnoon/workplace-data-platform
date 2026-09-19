-- Generated from semantic/metrics/headcount_incumbent.yml. Do not edit.
-- The number of people employed and attached to a workplace on a given day, whether or not they were expected in the office.

with daily as (
    select
        workplace_code,
        local_date,
        iso_year_week as period,
        region,
        sum(headcount_incumbent) as headcount_incumbent
    from main_marts.fct_workplace_capacity_daily
    group by workplace_code, local_date, iso_year_week, region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        avg(headcount_incumbent) as headcount_incumbent
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(headcount_incumbent) as headcount_incumbent
from per_workplace
group by period, region
order by period, region
