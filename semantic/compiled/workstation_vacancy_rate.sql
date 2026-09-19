-- Generated from semantic/metrics/workstation_vacancy_rate.yml. Do not edit.
-- The share of delivered desks left empty on the day, measured against everything built rather than against what was made available.

with daily as (
    select
        workplace_code,
        local_date,
        iso_year_week as period,
        region,
        sum(delivered_workstations) - sum(attendance_actual) as workstation_vacancy_rate_numerator,
        sum(delivered_workstations) as workstation_vacancy_rate_denominator
    from main_marts.fct_workplace_capacity_daily
    where (not is_weekend)
    group by workplace_code, local_date, iso_year_week, region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        avg(workstation_vacancy_rate_numerator) as workstation_vacancy_rate_numerator,
        avg(workstation_vacancy_rate_denominator) as workstation_vacancy_rate_denominator
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(workstation_vacancy_rate_numerator) / nullif(sum(workstation_vacancy_rate_denominator), 0) as workstation_vacancy_rate,
    sum(workstation_vacancy_rate_numerator) as workstation_vacancy_rate_numerator,
    sum(workstation_vacancy_rate_denominator) as workstation_vacancy_rate_denominator
from per_workplace
group by period, region
order by period, region
