-- Grain uniqueness on every fact table.
--
-- A duplicated grain row is the most expensive bug in a warehouse because it
-- does not look like a bug: every count is simply slightly too high, and it
-- stays that way until someone reconciles by hand. One test, three tables.

with violations as (

    select 'int_employee_day' as model, count(*) as offending_groups
    from (
        select emp_id, local_date
        from {{ ref('int_employee_day') }}
        group by 1, 2
        having count(*) > 1
    )

    union all

    select 'fct_attendance_daily', count(*)
    from (
        select local_date, workplace_code, dept_l2
        from {{ ref('fct_attendance_daily') }}
        group by 1, 2, 3
        having count(*) > 1
    )

    union all

    select 'fct_workplace_capacity_daily', count(*)
    from (
        select local_date, workplace_code
        from {{ ref('fct_workplace_capacity_daily') }}
        group by 1, 2
        having count(*) > 1
    )

    union all

    select 'fct_floor_presence_hourly', count(*)
    from (
        select local_date, workplace_code, tower, floor, local_hour
        from {{ ref('fct_floor_presence_hourly') }}
        group by 1, 2, 3, 4, 5
        having count(*) > 1
    )

)

select * from violations where offending_groups > 0
