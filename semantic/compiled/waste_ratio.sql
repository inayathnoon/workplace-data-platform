-- Generated from semantic/metrics/waste_ratio.yml. Do not edit.
-- The share of available desks that went unused on the day. The headline efficiency number for an estate.

with daily as (
    select
        workplace_code,
        local_date,
        iso_year_week as period,
        region,
        sum(case when region = 'CN' then allocated_workstations else allocated_workstations + free_sharing_workstations end) - sum(attendance_actual) as waste_ratio_numerator,
        sum(case when region = 'CN' then allocated_workstations else allocated_workstations + free_sharing_workstations end) as waste_ratio_denominator
    from main_marts.fct_workplace_capacity_daily
    where (not is_weekend)
    group by workplace_code, local_date, iso_year_week, region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        avg(waste_ratio_numerator) as waste_ratio_numerator,
        avg(waste_ratio_denominator) as waste_ratio_denominator
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(waste_ratio_numerator) / nullif(sum(waste_ratio_denominator), 0) as waste_ratio,
    sum(waste_ratio_numerator) as waste_ratio_numerator,
    sum(waste_ratio_denominator) as waste_ratio_denominator
from per_workplace
group by period, region
order by period, region
