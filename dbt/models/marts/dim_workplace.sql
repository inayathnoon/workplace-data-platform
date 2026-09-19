select
    w.workplace_code,
    w.workplace_name,
    w.city,
    w.country,
    w.region,
    w.timezone,
    w.has_missing_timezone,
    w.region = 'CN'                     as is_china_region,
    w.tower_count,
    w.floor_count,
    w.delivered_workstations,
    w.allocated_workstations,
    w.free_sharing_workstations,
    w.delivered_workstations - w.allocated_workstations as unallocated_workstations,
    -- Regional reconciliation. In CN the free-sharing pool is already inside
    -- allocated_workstations, so adding them double-counts. Resolved once,
    -- here, rather than in every consuming query.
    case
        when w.region = 'CN' then w.allocated_workstations
        else w.allocated_workstations + w.free_sharing_workstations
    end as available_workstations,
    w.net_workstation_area_sqm,
    round(w.net_workstation_area_sqm / nullif(w.delivered_workstations, 0), 2) as sqm_per_workstation,
    w.cost_per_workstation_month,
    w.lease_expiry_date,
    w.is_space_audited
from {{ ref('stg_workplace') }} w
