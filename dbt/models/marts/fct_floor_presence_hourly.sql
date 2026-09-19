-- Floor-level presence by local hour.
--
-- Derived from tap events, so it measures MOVEMENT past a reader, not dwell
-- time: someone at their desk all afternoon appears in the hour they arrived
-- and the hour they left, not in the hours between. Floor-level dwell needs
-- sensor data this platform does not have, and inferring it from two taps
-- would be a guess dressed as a measurement.

select
    local_date,
    workplace_code,
    tower,
    floor,
    cast(local_hour as integer)                 as local_hour,
    count(*)                                    as tap_events,
    count(distinct emp_id)                      as employees_seen,
    count(*) filter (where direction = 'in')    as tap_ins,
    count(*) filter (where direction = 'out')   as tap_outs
from {{ ref('stg_badge_taps') }}
group by 1, 2, 3, 4, 5
