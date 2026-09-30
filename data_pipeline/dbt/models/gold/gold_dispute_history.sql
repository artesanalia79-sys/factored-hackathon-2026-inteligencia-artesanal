{#- Serving table dispute_history: prior complaints and claims of served customers.
    related_transaction_id is always NULL: the source never links a complaint to a transaction
    (affected_product_id always points to another customer's product, data_audit.md D1), so no
    join is invented. -#}

with complaints as (
    select * from {{ ref('silver_complaints') }}
),

customers as (
    select customer_id from {{ ref('gold_customer_profile_min') }}
),

final as (
    select
        m.complaint_id,
        m.customer_id,
        m.creation_ts as created_at,
        m.case_type,
        m.category,
        m.subcategory,
        m.status,
        m.claimed_amount,
        m.currency,
        cast(null as varchar) as related_transaction_id,
        m.resolution_days
    from complaints as m
    inner join customers as c on c.customer_id = m.customer_id
    where m.creation_ts is not null
        and m.case_type is not null
        and m.category is not null
        and m.status is not null
)

select * from final
