-- Daily HR snapshot, typed and with reported-vs-derived status separated.
--
-- `employment_status_reported` is what HR said. `employment_status_derived` is
-- what the employee's own hire and termination dates imply. Keeping both is the
-- point: their disagreement is the defect the DQ layer is looking for, and
-- collapsing them here would erase the evidence before anyone sees it.

with typed as (

    select
        cast(snapshot_date as date)         as snapshot_date,
        emp_id,
        employment_status                   as employment_status_reported,
        cast(hire_date as date)             as hire_date,
        cast(term_date as date)             as term_date,
        badge_type,
        employee_type,
        dept_l1,
        dept_l2,
        dept_l3,
        dept_l4,
        base_city,
        theory_workplace,
        manager_id,
        assignment_city
    from {{ source('raw', 'hr_employee_snapshot') }}

)

select
    *,
    case
        when term_date is not null and snapshot_date > term_date then 'terminated'
        when snapshot_date < hire_date                           then 'onboarding'
        when assignment_city is not null
             and employment_status_reported = 'on_assignment'    then 'on_assignment'
        else 'active'
    end as employment_status_derived,
    term_date is not null and snapshot_date > term_date          as is_post_exit,
    snapshot_date < hire_date                                    as is_pre_hire
from typed
