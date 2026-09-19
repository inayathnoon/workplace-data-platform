-- Generated from semantic/metrics/seat_demand_absolute.yml. Do not edit.
-- The number of desks a workplace needs, in desks rather than as a share. This is the number that goes into a lease decision.

with c_seat_demand_rate as (
    with c_peak_day_attendance as (
        with daily as (
            select
                workplace_code,
                local_date,
                iso_year_week as period,
                region,
                max(attendance_actual) as peak_day_attendance
            from main_marts.fct_workplace_capacity_daily
            where (not is_weekend)
            group by workplace_code, local_date, iso_year_week, region
        ),
        
        per_workplace as (
            select
                workplace_code,
                period,
                region,
                max(peak_day_attendance) as peak_day_attendance
            from daily
            group by workplace_code, period, region
        )
        
        select
            period,
            region,
            sum(peak_day_attendance) as peak_day_attendance
        from per_workplace
        group by period, region
        order by period, region
    ),
    c_headcount_incumbent as (
        with daily as (
            select
                workplace_code,
                local_date,
                iso_year_week as period,
                region,
                sum(headcount_incumbent) as headcount_incumbent
            from main_marts.fct_workplace_capacity_daily
            group by workplace_code, local_date, iso_year_week, region
        ),
        
        per_workplace as (
            select
                workplace_code,
                period,
                region,
                avg(headcount_incumbent) as headcount_incumbent
            from daily
            group by workplace_code, period, region
        )
        
        select
            period,
            region,
            sum(headcount_incumbent) as headcount_incumbent
        from per_workplace
        group by period, region
        order by period, region
    )
    select c_peak_day_attendance.period, c_peak_day_attendance.region, (c_peak_day_attendance.peak_day_attendance / nullif(c_headcount_incumbent.headcount_incumbent, 0)) * (1 + 0.1) as seat_demand_rate
    from c_peak_day_attendance
    inner join c_headcount_incumbent on c_peak_day_attendance.period = c_headcount_incumbent.period and c_peak_day_attendance.region = c_headcount_incumbent.region
    order by c_peak_day_attendance.period, c_peak_day_attendance.region
),
c_headcount_incumbent as (
    with daily as (
        select
            workplace_code,
            local_date,
            iso_year_week as period,
            region,
            sum(headcount_incumbent) as headcount_incumbent
        from main_marts.fct_workplace_capacity_daily
        group by workplace_code, local_date, iso_year_week, region
    ),
    
    per_workplace as (
        select
            workplace_code,
            period,
            region,
            avg(headcount_incumbent) as headcount_incumbent
        from daily
        group by workplace_code, period, region
    )
    
    select
        period,
        region,
        sum(headcount_incumbent) as headcount_incumbent
    from per_workplace
    group by period, region
    order by period, region
)
select c_seat_demand_rate.period, c_seat_demand_rate.region, c_seat_demand_rate.seat_demand_rate * c_headcount_incumbent.headcount_incumbent as seat_demand_absolute
from c_seat_demand_rate
inner join c_headcount_incumbent on c_seat_demand_rate.period = c_headcount_incumbent.period and c_seat_demand_rate.region = c_headcount_incumbent.region
order by c_seat_demand_rate.period, c_seat_demand_rate.region
