-- Date spine covering the generated window, built from the fact rather than a
-- hardcoded range so it can never be narrower than the data it serves.

with bounds as (
    select min(local_date) as min_date, max(local_date) as max_date
    from {{ ref('int_employee_day') }}
),

spine as (
    select cast(unnest(generate_series(min_date, max_date, interval '1 day')) as date) as date_day
    from bounds
)

select
    date_day,
    extract(year from date_day)                                     as year,
    extract(month from date_day)                                    as month,
    extract(day from date_day)                                      as day_of_month,
    cast(extract(dow from date_day) as integer)                     as day_of_week_sun0,
    case when extract(dow from date_day) = 0 then 6
         else cast(extract(dow from date_day) as integer) - 1 end   as weekday_mon0,
    extract(week from date_day)                                     as iso_week,
    strftime(date_day, '%Y-W%V')                                    as iso_year_week,
    extract(dow from date_day) in (0, 6)                            as is_weekend,
    strftime(date_day, '%A')                                        as day_name
from spine
