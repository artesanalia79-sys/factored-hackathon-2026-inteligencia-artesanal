-- Per product_status: products with any transaction, expired products (expiration_date before
-- the last process_date), last_updated_ts after the last transaction (future values), and whether
-- products.last_transaction_ts agrees with the transactions table. Aggregates only.
with last_txn as (
    select product_id, max(transaction_ts) as max_ts
    from {{ ref('silver_transactions') }}
    group by 1
),
bounds as (
    select max(process_date) as last_day, max(transaction_ts) as last_ts
    from {{ ref('silver_transactions') }}
)
select
    p.product_status,
    count(*) as products,
    count(l.product_id) as products_with_transactions,
    count(p.expiration_date) as with_expiration_date,
    count(*) filter (where p.expiration_date < b.last_day) as expired,
    count(*) filter (where p.last_updated_ts > b.last_ts) as last_updated_in_future,
    count(p.last_transaction_ts) as with_last_transaction_ts,
    count(*) filter (
        where cast(p.last_transaction_ts as date) = cast(l.max_ts as date)
    ) as last_transaction_ts_matches
from {{ ref('silver_products') }} as p
cross join bounds as b
left join last_txn as l on l.product_id = p.product_id
group by 1
order by 1
