-- Confirmed travel, expanded to one row per employee-day.
--
-- Same overlap treatment as leave: an employee with two confirmed trips that
-- touch is still one person, on one day, in one place.

with expanded as (

    select
        t.emp_id,
        t.booking_id,
        t.destination_city,
        t.origin_city,
        cast(unnest(generate_series(t.depart_date, t.return_date, interval '1 day')) as date)
            as travel_date
    from {{ ref('stg_travel_bookings') }} t
    where t.is_effective

)

select
    e.emp_id,
    e.travel_date,
    e.destination_city,
    e.origin_city,
    e.booking_id,
    c.primary_workplace_code as destination_workplace_code
from expanded e
left join {{ ref('stg_city_primary_workplace') }} c
    on e.destination_city = c.city
qualify row_number() over (
    partition by e.emp_id, e.travel_date
    order by e.booking_id
) = 1
