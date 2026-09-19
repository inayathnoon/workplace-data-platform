-- Validity intervals derived from a daily full snapshot.
--
-- The HR system publishes every employee every day, so 98% of the rows are
-- restatements of yesterday. Collapsing them into versioned intervals is what
-- makes "what was this person's department on 3 March" a cheap question, and
-- it shrinks the snapshot by roughly the ratio of employees to employee-days.
--
-- Change detection is a hash of the attributes we care about, not of the whole
-- row: a column we do not model changing should not open a new version.

with hashed as (

    select
        *,
        md5(concat_ws('|',
            employment_status_reported,
            dept_l1, dept_l2, dept_l3, dept_l4,
            coalesce(base_city, '<null>'),
            theory_workplace,
            coalesce(manager_id, '<null>'),
            coalesce(assignment_city, '<null>'),
            employee_type,
            badge_type
        )) as attribute_hash
    from {{ ref('stg_hr_employee_snapshot') }}

),

marked as (

    select
        *,
        case
            when lag(attribute_hash) over (partition by emp_id order by snapshot_date)
                 is distinct from attribute_hash
            then 1 else 0
        end as is_version_start
    from hashed

),

versioned as (

    select
        *,
        sum(is_version_start) over (
            partition by emp_id
            order by snapshot_date
            rows between unbounded preceding and current row
        ) as version_number
    from marked

)

select
    emp_id,
    version_number,
    min(snapshot_date)                              as valid_from,
    max(snapshot_date)                              as valid_to,
    count(*)                                        as snapshot_days,
    max(snapshot_date) = max(max(snapshot_date)) over (partition by emp_id) as is_current,
    any_value(employment_status_reported)           as employment_status_reported,
    any_value(hire_date)                            as hire_date,
    any_value(term_date)                            as term_date,
    any_value(badge_type)                           as badge_type,
    any_value(employee_type)                        as employee_type,
    any_value(dept_l1)                              as dept_l1,
    any_value(dept_l2)                              as dept_l2,
    any_value(dept_l3)                              as dept_l3,
    any_value(dept_l4)                              as dept_l4,
    any_value(base_city)                            as base_city,
    any_value(theory_workplace)                     as theory_workplace,
    any_value(manager_id)                           as manager_id,
    any_value(assignment_city)                      as assignment_city
from versioned
group by emp_id, version_number
