{#- Call-center baseline for T16 (problem evidence and ROI), not a serving table: the agent never
    reads it. Grain: one row per (month, country). month buckets process_date (the source's
    batch date, data_audit.md A1); country is the customer's. Counts are kept next to every rate
    so T16 can re-aggregate. The first and last months are partial: see days_with_data. -#}

with interactions as (
    select * from {{ ref('silver_call_center_interactions') }}
),

customers as (
    select customer_id, country_code from {{ ref('silver_customers') }}
),

final as (
    select
        cast(date_trunc('month', i.process_date) as date) as month,
        c.country_code as country,
        cast(count(distinct i.process_date) as integer) as days_with_data,
        count(*) as contacts,
        count(*) filter (where i.was_resolved) as resolved_contacts,
        count(*) filter (where i.was_escalated) as escalated_contacts,
        count(i.duration_seconds) as contacts_with_duration,
        cast(coalesce(sum(i.duration_seconds), 0) as bigint) as total_duration_seconds,
        avg(i.duration_seconds) as avg_duration_seconds,
        count(*) filter (where i.was_resolved) / count(*) as resolution_rate,
        count(*) filter (where i.was_escalated) / count(*) as escalation_rate
    from interactions as i
    left join customers as c on c.customer_id = i.customer_id
    where i.process_date is not null
    group by 1, 2
)

select * from final
