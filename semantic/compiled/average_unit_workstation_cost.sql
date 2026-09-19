-- Generated from semantic/metrics/average_unit_workstation_cost.yml. Do not edit.
-- The average monthly cost of one desk across a group of workplaces, weighted by how many desks each workplace has.

with daily as (
    select
        workplace_code,
        local_date,
        strftime(local_date, '%Y-%m') as period,
        region,
        sum(cost_per_workstation_month * delivered_workstations) as average_unit_workstation_cost_numerator,
        sum(delivered_workstations) as average_unit_workstation_cost_denominator
    from main_marts.fct_workplace_capacity_daily
    group by workplace_code, local_date, strftime(local_date, '%Y-%m'), region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        avg(average_unit_workstation_cost_numerator) as average_unit_workstation_cost_numerator,
        avg(average_unit_workstation_cost_denominator) as average_unit_workstation_cost_denominator
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(average_unit_workstation_cost_numerator) / nullif(sum(average_unit_workstation_cost_denominator), 0) as average_unit_workstation_cost,
    sum(average_unit_workstation_cost_numerator) as average_unit_workstation_cost_numerator,
    sum(average_unit_workstation_cost_denominator) as average_unit_workstation_cost_denominator
from per_workplace
group by period, region
order by period, region
