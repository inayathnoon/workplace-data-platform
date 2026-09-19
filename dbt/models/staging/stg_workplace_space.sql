-- Floor-grain space extract, cleaned.
--
-- The timezone fallback is the important line here. A workplace with a missing
-- timezone would otherwise shift its whole attendance to the wrong local date,
-- silently. We fall back to UTC, keep a flag saying we did, and let the DQ
-- layer report it - rather than guessing a plausible zone from the country and
-- making a wrong answer look like a right one.

select
    workplace_code,
    name                                as workplace_name,
    city,
    country,
    region,
    coalesce(timezone, 'UTC')           as timezone,
    timezone is null                    as has_missing_timezone,
    tower,
    floor,
    delivered_workstations,
    allocated_workstations,
    free_sharing_workstations,
    net_workstation_area_sqm,
    cost_per_workstation_month,
    cast(lease_expiry_date as date)     as lease_expiry_date,
    is_space_audited
from {{ source('raw', 'workplace_dim') }}
