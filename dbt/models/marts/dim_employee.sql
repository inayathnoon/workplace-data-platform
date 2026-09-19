-- Current-version employee dimension.
--
-- Type 2 history lives in int_employee_scd2; this is the type 1 view of it that
-- dashboards join to. The valid_from/valid_to columns are carried through so a
-- consumer that needs point-in-time can follow the trail back without guessing
-- which model holds it.

select
    s.emp_id,
    s.version_number,
    s.valid_from,
    s.valid_to,
    s.employment_status_reported,
    s.hire_date,
    s.term_date,
    s.badge_type,
    s.employee_type,
    s.dept_l1,
    s.dept_l2,
    s.dept_l3,
    s.dept_l4,
    s.base_city,
    s.theory_workplace,
    s.manager_id,
    s.assignment_city,
    w.city          as theory_city,
    w.region        as theory_region,
    w.country       as theory_country,
    s.base_city is null as has_missing_base_city
from {{ ref('int_employee_scd2') }} s
left join {{ ref('stg_workplace') }} w
    on s.theory_workplace = w.workplace_code
where s.is_current
