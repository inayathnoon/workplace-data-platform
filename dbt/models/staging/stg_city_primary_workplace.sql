-- The workplace someone is assumed to use when all we know is the city.
--
-- Used for travel destinations and assignment cities, where the source tells us
-- a city and not a building. Largest delivered capacity wins, with the code as
-- a deterministic tie-break so the mapping never flips between runs.

select
    city,
    region,
    workplace_code as primary_workplace_code
from (
    select
        city,
        region,
        workplace_code,
        row_number() over (
            partition by city
            order by delivered_workstations desc, workplace_code
        ) as rn
    from {{ ref('stg_workplace') }}
)
where rn = 1
