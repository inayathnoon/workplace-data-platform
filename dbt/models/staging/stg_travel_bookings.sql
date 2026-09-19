select
    booking_id,
    emp_id,
    origin_city,
    destination_city,
    cast(depart_date as date)   as depart_date,
    cast(return_date as date)   as return_date,
    booking_status,
    hotel_flag,
    flight_flag,
    booking_status = 'confirmed' as is_effective,
    date_diff('day', cast(depart_date as date), cast(return_date as date)) + 1 as trip_days
from {{ source('raw', 'travel_bookings') }}
