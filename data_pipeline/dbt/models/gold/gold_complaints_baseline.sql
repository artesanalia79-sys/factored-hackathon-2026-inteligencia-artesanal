{#- Complaints baseline for T16 (problem evidence and ROI), not a serving table. Grain: one row
    per (month, country, case_type, category). month buckets process_date; country is the
    customer's. A complaint counts as resolved when its status is Resolved or Closed (the only
    statuses that carry resolution_days). Counts are kept next to every rate. -#}

with complaints as (
    select * from {{ ref('silver_complaints') }}
),

customers as (
    select customer_id, country_code from {{ ref('silver_customers') }}
),

final as (
    select
        cast(date_trunc('month', m.process_date) as date) as month,
        c.country_code as country,
        m.case_type,
        m.category,
        cast(count(distinct m.process_date) as integer) as days_with_data,
        count(*) as complaints,
        count(*) filter (where m.status in ('Resolved', 'Closed')) as resolved_complaints,
        count(m.resolution_days) as complaints_with_resolution_days,
        avg(m.resolution_days) as avg_resolution_days,
        count(*) filter (where m.sla_breached) as sla_breached_complaints,
        count(m.sla_breached) as complaints_with_sla,
        count(*) filter (where m.sla_breached) / nullif(count(m.sla_breached), 0)
            as sla_breach_rate
    from complaints as m
    left join customers as c on c.customer_id = m.customer_id
    where m.process_date is not null
    group by 1, 2, 3, 4
)

select * from final
