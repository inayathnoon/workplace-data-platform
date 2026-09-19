-- Generated from semantic/metrics/workstation_waste.yml. Do not edit.
-- Desks that were available on the day but nobody used. Not the same as empty desks: a desk nobody is allocated to and nobody sat at counts once, not twice.

with daily as (
    select
        workplace_code,
        local_date,
        iso_year_week as period,
        region,
        sum(case when region = 'CN' then allocated_workstations else allocated_workstations + free_sharing_workstations end) - sum(attendance_actual) as workstation_waste
    from main_marts.fct_workplace_capacity_daily
    where (not is_weekend)
    group by workplace_code, local_date, iso_year_week, region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        avg(workstation_waste) as workstation_waste
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(workstation_waste) as workstation_waste
from per_workplace
group by period, region
order by period, region
