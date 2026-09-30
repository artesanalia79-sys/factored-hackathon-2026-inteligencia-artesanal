-- T16, offline only (ADR 0003: is_fraud is a label, never a runtime feature): does fraud repeat
-- on a card after its first fraudulent transaction? If it did, a faster card block would avoid
-- measurable losses. Window of 37 h = median first-response time of disputes
-- (t16_dispute_status.sql). Aggregates only.
with fraud as (
    select
        product_id,
        transaction_ts,
        amount_usd,
        row_number() over (partition by product_id order by transaction_ts) as nth_fraud,
        min(transaction_ts) over (partition by product_id) as first_fraud_ts
    from {{ ref('silver_transactions') }}
    where is_fraud
)

select
    count(distinct product_id) as products_with_fraud,
    count(*) as fraud_transactions,
    count(*) filter (where nth_fraud > 1) as later_fraud_transactions,
    count(*) filter (
        where nth_fraud > 1 and transaction_ts <= first_fraud_ts + interval 37 hour
    ) as later_fraud_within_37h,
    median(amount_usd) as median_fraud_amount_usd
from fraud
