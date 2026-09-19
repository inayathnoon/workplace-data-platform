-- Generated from semantic/metrics/scheduled_office_day_compliance_rate.yml. Do not edit.
-- Of all days the office-day policy required attendance, the share on which the employee attended. Unlike attendance rate, leave and travel are not excused.

with daily as (
    select
        workplace_code,
        local_date,
        iso_year_week as period,
        region,
        sum(scheduled_attendance) as scheduled_office_day_compliance_rate_numerator,
        sum(scheduled_office_days) as scheduled_office_day_compliance_rate_denominator
    from main_marts.fct_attendance_daily
    group by workplace_code, local_date, iso_year_week, region
),

per_workplace as (
    select
        workplace_code,
        period,
        region,
        avg(scheduled_office_day_compliance_rate_numerator) as scheduled_office_day_compliance_rate_numerator,
        avg(scheduled_office_day_compliance_rate_denominator) as scheduled_office_day_compliance_rate_denominator
    from daily
    group by workplace_code, period, region
)

select
    period,
    region,
    sum(scheduled_office_day_compliance_rate_numerator) / nullif(sum(scheduled_office_day_compliance_rate_denominator), 0) as scheduled_office_day_compliance_rate,
    sum(scheduled_office_day_compliance_rate_numerator) as scheduled_office_day_compliance_rate_numerator,
    sum(scheduled_office_day_compliance_rate_denominator) as scheduled_office_day_compliance_rate_denominator
from per_workplace
group by period, region
order by period, region
