-- Every employee-day gets exactly one state, and the state agrees with the
-- flags that produced it.
--
-- The cascade in int_employee_day is written as a CASE, so "exactly one" is
-- true by construction - which is precisely why it is worth testing. The value
-- is in the second half: a state that contradicts its own evidence (a row
-- labelled on_leave with is_on_leave false) means someone reordered the cascade
-- without reordering the labels.

select
    emp_id,
    local_date,
    employee_day_state,
    state_rule_id
from {{ ref('int_employee_day') }}
where employee_day_state is null
   or state_rule_id is null
   or (employee_day_state = 'on_leave'       and not is_on_leave)
   or (employee_day_state = 'on_travel'      and not is_on_travel)
   or (employee_day_state = 'on_assignment'  and not is_on_assignment)
   or (employee_day_state = 'attended'       and not attended_flag)
   or (employee_day_state = 'no_show'        and attended_flag)
   or (employee_day_state = 'post_exit'      and employment_status_effective != 'terminated')
   or (employee_day_state = 'no_show'        and not is_scheduled_office_day)
