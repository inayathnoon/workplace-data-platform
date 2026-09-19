-- Generated from semantic/metrics/unallocated_workstation_rate.yml. Do not edit.
-- The share of delivered desks not assigned to any department. Pure supply-side slack, visible without any attendance data at all.

with daily as (
    select
        workplace_code,
        local_date,
        iso_year_week as period,
        region,
        sum(unallocated_workstations) as unallocated_workstation_rate_numerator,
        sum(delivered_workstations) as unallocated_workstation_rate_denominator
    from main_marts.fct_workplace_capacity_daily
    group by workplace_code, local_date, iso_year_week, region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        avg(unallocated_workstation_rate_numerator) as unallocated_workstation_rate_numerator,
        avg(unallocated_workstation_rate_denominator) as unallocated_workstation_rate_denominator
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(unallocated_workstation_rate_numerator) / nullif(sum(unallocated_workstation_rate_denominator), 0) as unallocated_workstation_rate,
    sum(unallocated_workstation_rate_numerator) as unallocated_workstation_rate_numerator,
    sum(unallocated_workstation_rate_denominator) as unallocated_workstation_rate_denominator
from per_workplace
group by period, region
order by period, region
