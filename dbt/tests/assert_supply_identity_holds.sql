-- The capacity identities must hold exactly, on every workplace-day.
--
--   delivered   = allocated + unallocated
--   available   = allocated + free-sharing   (allocated alone in CN, where the
--                                             pool is already inside it)
--   waste       = available - attendance
--
-- These are definitions, not measurements, so any drift means a model has
-- started computing one of them a second way. Written as an equality test
-- because "close enough" on an identity is always a bug.

select
    local_date,
    workplace_code,
    delivered_workstations,
    allocated_workstations,
    unallocated_workstations,
    free_sharing_workstations,
    available_workstations,
    attendance_actual,
    workstation_waste
from {{ ref('fct_workplace_capacity_daily') }}
where delivered_workstations != allocated_workstations + unallocated_workstations
   or available_workstations != case
        when region = 'CN' then allocated_workstations
        else allocated_workstations + free_sharing_workstations
      end
   or workstation_waste != available_workstations - attendance_actual
   or allocated_workstations > delivered_workstations
