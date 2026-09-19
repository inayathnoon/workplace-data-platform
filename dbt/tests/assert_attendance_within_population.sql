-- No aggregate may exceed the population it is drawn from.
--
-- Attendance above headcount, or scheduled attendance above scheduled days, is
-- the signature of a fan-out join. It is the check that catches the overlapping
-- leave requests and the duplicate taps if their de-duplication is ever
-- removed, and it fails in an obvious place rather than as a slightly wrong
-- number on a dashboard.

select
    local_date,
    workplace_code,
    dept_l2,
    employee_days,
    headcount_incumbent,
    attendance_daily,
    scheduled_office_days,
    scheduled_attendance
from {{ ref('fct_attendance_daily') }}
where attendance_daily > employee_days
   or scheduled_attendance > scheduled_office_days
   or headcount_incumbent > employee_days
   or scheduled_attendance > attendance_daily
