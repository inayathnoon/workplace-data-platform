-- Generated from semantic/metrics/floating_headcount.yml. Do not edit.
-- People attached to a workplace with no allocated desk of their own. They rely on the free-sharing pool, so they are the population that pool must cover.

with daily as (
    select
        workplace_code,
        local_date,
        iso_year_week as period,
        region,
        sum(floating_headcount) as floating_headcount
    from main_marts.fct_workplace_capacity_daily
    where (not is_weekend)
    group by workplace_code, local_date, iso_year_week, region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        avg(floating_headcount) as floating_headcount
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(floating_headcount) as floating_headcount
from per_workplace
group by period, region
order by period, region
