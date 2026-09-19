-- Generated from semantic/metrics/incumbent_vacancy_rate.yml. Do not edit.
-- The share of allocated desks with nobody assigned to them. Measures the gap between allocation and population, independent of whether people attended.

with daily as (
    select
        workplace_code,
        local_date,
        iso_year_week as period,
        region,
        sum(allocated_workstations) - sum(headcount_incumbent) as incumbent_vacancy_rate_numerator,
        sum(allocated_workstations) as incumbent_vacancy_rate_denominator
    from main_marts.fct_workplace_capacity_daily
    where (not is_weekend)
    group by workplace_code, local_date, iso_year_week, region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        avg(incumbent_vacancy_rate_numerator) as incumbent_vacancy_rate_numerator,
        avg(incumbent_vacancy_rate_denominator) as incumbent_vacancy_rate_denominator
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(incumbent_vacancy_rate_numerator) / nullif(sum(incumbent_vacancy_rate_denominator), 0) as incumbent_vacancy_rate,
    sum(incumbent_vacancy_rate_numerator) as incumbent_vacancy_rate_numerator,
    sum(incumbent_vacancy_rate_denominator) as incumbent_vacancy_rate_denominator
from per_workplace
group by period, region
order by period, region
