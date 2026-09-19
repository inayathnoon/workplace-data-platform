-- Attendance at (local_date, expected_workplace, dept_l2) grain.
--
-- Keyed on where people were EXPECTED, not where they tapped. That is what
-- makes it the right table for compliance and the wrong table for occupancy -
-- fct_workplace_capacity_daily is keyed the other way for exactly that reason,
-- and the two answer different questions on purpose.

select
    ed.local_date,
    ed.expected_workplace                                       as workplace_code,
    w.city,
    w.region,
    ed.dept_l1,
    ed.dept_l2,

    count(*)                                                    as employee_days,
    count(*) filter (where ed.is_incumbent)                     as headcount_incumbent,
    count(*) filter (where ed.is_scheduled_office_day)          as scheduled_office_days,
    count(*) filter (where ed.attended_flag)                    as attendance_daily,
    count(*) filter (where ed.is_scheduled_office_day and ed.attended_flag)
                                                                as scheduled_attendance,
    count(*) filter (where ed.is_attendance_expected)           as attendance_expected_days,
    count(*) filter (where ed.is_attendance_expected and ed.attended_flag)
                                                                as attendance_expected_met,
    count(*) filter (where ed.employee_day_state = 'no_show')   as no_show_days,
    count(*) filter (where ed.is_on_leave)                      as leave_days,
    count(*) filter (where ed.is_on_travel)                     as travel_days,
    count(*) filter (where ed.is_on_assignment)                 as assignment_days,
    count(*) filter (where ed.employee_day_state = 'non_office_day') as non_office_days,
    count(*) filter (where ed.employee_day_state = 'post_exit') as post_exit_days,
    count(*) filter (where ed.employee_day_state = 'pre_hire')  as pre_hire_days,
    count(*) filter (where ed.has_late_reported_tap)            as late_reported_tap_days,

    avg(ed.span_minutes) filter (where ed.attended_flag)        as avg_span_minutes,
    max(ed.required_days_per_week)                              as required_days_per_week
from {{ ref('int_employee_day') }} ed
left join {{ ref('stg_workplace') }} w
    on ed.expected_workplace = w.workplace_code
group by 1, 2, 3, 4, 5, 6
