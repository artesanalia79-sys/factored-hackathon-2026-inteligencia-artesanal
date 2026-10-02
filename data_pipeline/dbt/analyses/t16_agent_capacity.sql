-- T16: declared agent capacity vs. observed contact volume. service_agents declares
-- total_monthly_interactions per agent; the interactions table holds far fewer contacts per
-- month. Their ratio is the volume-scale factor of the "declared capacity" scenario.
-- Aggregates only.
with observed as (
    select
        count(*) as contacts,
        count(distinct process_date) as days_with_data,
        count(distinct agent_id) as agents_seen,
        count(distinct customer_id) as customers_seen
    from {{ ref('silver_call_center_interactions') }}
),

declared as (
    select
        count(*) filter (where agent_status = 'Active') as active_agents,
        sum(total_monthly_interactions) filter (where agent_status = 'Active')
            as declared_monthly_interactions
    from {{ ref('silver_service_agents') }}
)

select
    d.active_agents,
    d.declared_monthly_interactions,
    o.agents_seen,
    o.customers_seen,
    o.contacts,
    o.days_with_data,
    o.contacts * 365.0 / 12 / o.days_with_data as observed_monthly_contacts,
    d.declared_monthly_interactions / (o.contacts * 365.0 / 12 / o.days_with_data)
        as declared_to_observed_ratio
from observed as o
cross join declared as d
