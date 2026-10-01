-- T16, offline only (ADR 0003: is_fraud is a label, never a runtime feature): does fraud repeat
-- on a card after its first fraudulent transaction? If it did, a faster card block would avoid
-- measurable losses. Card products only (credit and debit), the ones the agent can block.
-- Window of 37 h = median first-response time of disputes (t16_dispute_status.sql).
-- Aggregates only.
with fraud as (
    select
        t.product_id,
        t.transaction_ts,
        t.amount_usd,
        row_number() over (partition by t.product_id order by t.transaction_ts) as nth_fraud,
        min(t.transaction_ts) over (partition by t.product_id) as first_fraud_ts
    from {{ ref('silver_transactions') }} as t
    join {{ ref('silver_products') }} as p on p.product_id = t.product_id
    where t.is_fraud and p.product_type in ('Tarjeta Crédito', 'Tarjeta Débito')
)

select
    count(distinct product_id) as cards_with_fraud,
    count(*) as fraud_transactions,
    count(*) filter (where nth_fraud > 1) as later_fraud_transactions,
    count(*) filter (
        where nth_fraud > 1 and transaction_ts <= first_fraud_ts + interval 37 hour
    ) as later_fraud_within_37h,
    median(amount_usd) as median_fraud_amount_usd
from fraud
