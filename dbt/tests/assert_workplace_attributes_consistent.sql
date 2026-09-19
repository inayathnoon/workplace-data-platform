-- The space extract repeats workplace-level attributes on every floor row.
-- stg_workplace collapses them with min(); this asserts that min() is not
-- quietly discarding a disagreement.

select
    workplace_code,
    count(distinct city)                        as cities,
    count(distinct region)                      as regions,
    count(distinct timezone)                    as timezones,
    count(distinct cost_per_workstation_month)  as costs
from {{ ref('stg_workplace_space') }}
group by workplace_code
having count(distinct city) > 1
    or count(distinct region) > 1
    or count(distinct timezone) > 1
    or count(distinct cost_per_workstation_month) > 1
