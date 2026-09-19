-- Approved leave, expanded to one row per employee-day.
--
-- The de-duplication matters more than the expansion. Employees extend leave by
-- raising a second overlapping request rather than amending the first, so a
-- naive expansion double-counts the overlap and inflates absence. We keep one
-- row per (emp_id, leave_date), preferring the earliest request.

with expanded as (

    select
        emp_id,
        leave_id,
        leave_type,
        applied_ts,
        is_retro_applied,
        cast(unnest(generate_series(start_date, end_date, interval '1 day')) as date) as leave_date
    from {{ ref('stg_leave_requests') }}
    where is_effective

)

select
    emp_id,
    leave_date,
    leave_type,
    leave_id,
    is_retro_applied
from expanded
qualify row_number() over (
    partition by emp_id, leave_date
    order by applied_ts, leave_id
) = 1
