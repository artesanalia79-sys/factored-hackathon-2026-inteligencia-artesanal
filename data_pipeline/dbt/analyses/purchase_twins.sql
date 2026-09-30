-- Grounding for "double charge" disputes and merchant recognition: repeated charges (same
-- product, merchant and amount) and the number of categories per merchant. Aggregates only.
with purchases as (
    select
        product_id,
        merchant_name,
        amount,
        transaction_ts,
        lag(transaction_ts) over (
            partition by product_id, merchant_name, amount order by transaction_ts
        ) as previous_ts
    from {{ ref('silver_transactions') }}
    where transaction_type = 'Purchase' and merchant_name is not null
),
merchants as (
    select merchant_name, count(distinct merchant_category) as categories
    from {{ ref('silver_transactions') }}
    where merchant_name is not null
    group by 1
)
select
    count(*) as purchases_with_merchant,
    count(*) filter (where transaction_ts - previous_ts <= interval 10 minute) as twin_within_10_min,
    count(*) filter (where transaction_ts - previous_ts <= interval 1 day) as twin_within_1_day,
    count(previous_ts) as twin_any_time,
    (select count(*) from merchants) as merchants,
    (select max(categories) from merchants) as max_categories_per_merchant
from purchases
