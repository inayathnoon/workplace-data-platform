-- Leave at request grain, with the approval filter left to the consumer.
--
-- `is_effective` encodes the one rule everybody forgets: pending and cancelled
-- requests are not absence. Exposing it as a flag rather than filtering here
-- means a model that wants to count cancellations can still do so.

select
    leave_id,
    emp_id,
    leave_type,
    cast(start_date as date)                        as start_date,
    cast(end_date as date)                          as end_date,
    status,
    applied_ts,
    status = 'approved'                             as is_effective,
    cast(applied_ts as date) > cast(start_date as date) as is_retro_applied,
    date_diff('day', cast(start_date as date), cast(end_date as date)) + 1 as duration_days
from {{ source('raw', 'leave_requests') }}
