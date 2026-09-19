select
    dept_l2,
    dept_l1,
    weekday,
    is_scheduled_office_day,
    required_days_per_week,
    policy_version
from {{ source('raw', 'dept_schedule') }}
