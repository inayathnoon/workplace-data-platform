-- The central fact: one row per (emp_id, local_date). Everything else in the
-- warehouse is an aggregate of this table.
--
-- PRECEDENCE
-- An employee-day can satisfy several conditions at once - a terminated
-- employee can have an approved leave request covering a day they also badged
-- in on. Without an explicit order, two dashboards will pick different answers
-- and both will be defensible. The cascade below is the single declared order,
-- it is documented in the README, and it is tested by
-- assert_employee_day_state_is_exhaustive.
--
--   1. post_exit          the employee had left. Nothing else can be true.
--   2. pre_hire           the employee had not started yet.
--   3. on_leave           approved leave beats travel: an approved sick day
--                         during a booked trip is absence, not a trip.
--   4. on_travel          confirmed travel beats assignment.
--   5. on_assignment      posted to another city for a period.
--   6. non_office_day     not a scheduled office day for their division.
--   7. attended           a scheduled office day with a tap.
--   8. no_show            a scheduled office day with no tap and no reason.
--
-- `employment_status_effective` is derived from hire and termination dates, not
-- from the status HR reported. Where the two disagree the reported value is
-- kept alongside so the DQ layer can measure the disagreement instead of the
-- warehouse quietly absorbing it.

with hr as (

    select * from {{ ref('stg_hr_employee_snapshot') }}

),

joined as (

    select
        hr.emp_id,
        hr.snapshot_date                                as local_date,
        hr.dept_l1,
        hr.dept_l2,
        hr.dept_l3,
        hr.dept_l4,
        hr.employee_type,
        hr.badge_type,
        hr.manager_id,
        hr.base_city,
        hr.theory_workplace,
        hr.hire_date,
        hr.term_date,
        hr.employment_status_reported,
        hr.employment_status_derived,
        hr.is_post_exit,
        hr.is_pre_hire,
        hr.assignment_city,

        coalesce(sch.is_scheduled_office_day, false)    as is_scheduled_day_policy,
        sch.required_days_per_week,

        lv.leave_date is not null                       as is_on_leave,
        lv.leave_type,
        tv.travel_date is not null                      as is_on_travel,
        tv.destination_city,
        tv.destination_workplace_code,

        tap.first_tap_ts,
        tap.last_tap_ts,
        tap.tap_count,
        tap.tap_workplace,
        tap.tap_city,
        tap.tap_region,
        tap.tower_first,
        tap.tower_last,
        tap.floor_first,
        tap.floor_last,
        tap.span_minutes,
        tap.workplace_count,
        coalesce(tap.has_late_reported_tap, false)      as has_late_reported_tap,

        assign_wp.primary_workplace_code                as assignment_workplace_code
    from hr
    left join {{ ref('stg_dept_schedule') }} sch
        on hr.dept_l2 = sch.dept_l2
       and cast(extract(dow from hr.snapshot_date) as integer)
           = case when sch.weekday = 6 then 0 else sch.weekday + 1 end
    left join {{ ref('int_leave_days') }} lv
        on hr.emp_id = lv.emp_id and hr.snapshot_date = lv.leave_date
    left join {{ ref('int_travel_days') }} tv
        on hr.emp_id = tv.emp_id and hr.snapshot_date = tv.travel_date
    left join {{ ref('int_tap_day') }} tap
        on hr.emp_id = tap.emp_id and hr.snapshot_date = tap.local_date
    left join {{ ref('stg_city_primary_workplace') }} assign_wp
        on hr.assignment_city = assign_wp.city

),

resolved as (

    select
        *,
        -- Derived from dates, so a defective reported status cannot move it.
        case
            when is_post_exit                                   then 'terminated'
            when is_pre_hire                                    then 'onboarding'
            when assignment_city is not null
                 and employment_status_reported = 'on_assignment' then 'on_assignment'
            when employment_status_reported = 'pre_assignment'  then 'pre_assignment'
            else 'active'
        end as employment_status_effective,

        first_tap_ts is not null                                as attended_flag,

        -- An employee only owes attendance if they are employed and not posted
        -- elsewhere; the policy flag alone would charge leavers with no-shows.
        is_scheduled_day_policy
            and not is_post_exit
            and not is_pre_hire                                 as is_scheduled_office_day,

        -- The days attendance was actually OWED: scheduled, employed, and not
        -- excused by leave, travel or an out-of-city posting. This is the
        -- denominator the attendance rate should use. Using scheduled days
        -- instead charges an approved absence as a no-show, which is the
        -- single most common way this metric gets quietly overstated.
        is_scheduled_day_policy
            and not is_post_exit
            and not is_pre_hire
            and not is_on_leave
            and not is_on_travel
            and not (assignment_city is not null
                     and employment_status_reported = 'on_assignment') as is_attendance_expected,

        case
            when is_post_exit                                   then 1
            when is_pre_hire                                    then 2
            when is_on_leave                                    then 3
            when is_on_travel                                   then 4
            when assignment_city is not null
                 and employment_status_reported = 'on_assignment' then 5
            when not is_scheduled_day_policy                    then 6
            when first_tap_ts is not null                       then 7
            else 8
        end as state_rule_id
    from joined

)

select
    emp_id,
    local_date,
    dept_l1,
    dept_l2,
    dept_l3,
    dept_l4,
    employee_type,
    badge_type,
    manager_id,
    base_city,
    hire_date,
    term_date,
    employment_status_reported,
    employment_status_effective,
    assignment_city,

    -- Where we expected them: assignment beats travel beats their home desk.
    coalesce(
        case when state_rule_id = 5 then assignment_workplace_code end,
        case when state_rule_id = 4 then destination_workplace_code end,
        theory_workplace
    ) as expected_workplace,
    theory_workplace,

    is_scheduled_office_day,
    is_attendance_expected,
    required_days_per_week,
    is_on_leave,
    leave_type,
    is_on_travel,
    destination_city,
    state_rule_id = 5                                       as is_on_assignment,

    attended_flag,
    first_tap_ts,
    last_tap_ts,
    tap_workplace,
    tap_city,
    tap_region,
    tower_first,
    tower_last,
    floor_first,
    floor_last,
    tap_count,
    workplace_count,
    span_minutes,
    has_late_reported_tap,

    state_rule_id,
    case state_rule_id
        when 1 then 'post_exit'
        when 2 then 'pre_hire'
        when 3 then 'on_leave'
        when 4 then 'on_travel'
        when 5 then 'on_assignment'
        when 6 then 'non_office_day'
        when 7 then 'attended'
        else        'no_show'
    end as employee_day_state,

    -- An employee is an incumbent if they were employed on the day, whether or
    -- not they were expected in. Headcount denominators use this, never the
    -- reported status, which is the column the planted defect corrupts.
    not is_post_exit and not is_pre_hire                    as is_incumbent
from resolved
