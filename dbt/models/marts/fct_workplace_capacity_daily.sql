-- Supply against realised demand, at (local_date, workplace_code) grain.
--
-- Keyed on where people ACTUALLY tapped, because a desk is occupied by whoever
-- sits at it, not by whoever was rostered to. Supply columns are repeated on
-- every day rather than joined at query time: the space extract is a slowly
-- changing snapshot and carrying it inline keeps every capacity metric a
-- single-table scan.

with presence as (

    select
        local_date,
        tap_workplace   as workplace_code,
        count(*)        as attendance_actual,
        count(distinct dept_l2) as departments_present
    from {{ ref('int_employee_day') }}
    where attended_flag
    group by 1, 2

),

expected as (

    select
        local_date,
        expected_workplace as workplace_code,
        count(*) filter (where is_incumbent) as headcount_incumbent
    from {{ ref('int_employee_day') }}
    group by 1, 2

),

spine as (

    select d.date_day as local_date, w.workplace_code
    from {{ ref('dim_date') }} d
    cross join {{ ref('dim_workplace') }} w

)

select
    s.local_date,
    s.workplace_code,
    w.city,
    w.region,
    w.is_china_region,
    d.is_weekend,
    d.iso_year_week,

    w.delivered_workstations,
    w.allocated_workstations,
    w.free_sharing_workstations,
    w.unallocated_workstations,
    w.available_workstations,
    w.cost_per_workstation_month,
    w.net_workstation_area_sqm,

    coalesce(e.headcount_incumbent, 0)                      as headcount_incumbent,
    coalesce(p.attendance_actual, 0)                        as attendance_actual,
    coalesce(p.departments_present, 0)                      as departments_present,

    w.available_workstations - coalesce(p.attendance_actual, 0) as workstation_waste,
    greatest(coalesce(e.headcount_incumbent, 0) - w.allocated_workstations, 0)
                                                            as floating_headcount
from spine s
inner join {{ ref('dim_workplace') }} w on s.workplace_code = w.workplace_code
inner join {{ ref('dim_date') }} d      on s.local_date = d.date_day
left join presence p on s.local_date = p.local_date and s.workplace_code = p.workplace_code
left join expected e on s.local_date = e.local_date and s.workplace_code = e.workplace_code
