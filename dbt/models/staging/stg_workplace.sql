-- Workplace-grain rollup of the floor-grain space extract.
--
-- Cost and timezone are workplace-level attributes that the source repeats on
-- every floor row; min() is a deliberate choice of "pick one" that would fail
-- loudly via the singular test assert_workplace_attributes_consistent if the
-- source ever started disagreeing with itself across floors.

select
    workplace_code,
    min(workplace_name)                     as workplace_name,
    min(city)                               as city,
    min(country)                            as country,
    min(region)                             as region,
    min(timezone)                           as timezone,
    bool_or(has_missing_timezone)           as has_missing_timezone,
    count(*)                                as floor_count,
    count(distinct tower)                   as tower_count,
    sum(delivered_workstations)             as delivered_workstations,
    sum(allocated_workstations)             as allocated_workstations,
    sum(free_sharing_workstations)          as free_sharing_workstations,
    sum(net_workstation_area_sqm)           as net_workstation_area_sqm,
    min(cost_per_workstation_month)         as cost_per_workstation_month,
    min(lease_expiry_date)                  as lease_expiry_date,
    bool_and(is_space_audited)              as is_space_audited
from {{ ref('stg_workplace_space') }}
group by workplace_code
