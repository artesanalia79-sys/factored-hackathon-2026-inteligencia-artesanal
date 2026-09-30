{#- Serving table customer_cards. Credit and debit cards only (silver card_type), owned by a
    served customer, with every contract column present. Only card_last4 leaves silver; the full
    product_number never does. -#}

with products as (
    select * from {{ ref('silver_products') }}
),

customers as (
    select customer_id from {{ ref('gold_customer_profile_min') }}
),

final as (
    select
        p.product_id,
        p.customer_id,
        p.card_type,
        p.card_last4,
        p.currency,
        p.product_status,
        p.opening_date,
        p.expiration_date
    from products as p
    inner join customers as c on c.customer_id = p.customer_id
    where p.card_type in ('credit', 'debit')
        and p.card_last4 is not null
        and p.currency is not null
        and p.product_status is not null
        and p.opening_date is not null
)

select * from final
