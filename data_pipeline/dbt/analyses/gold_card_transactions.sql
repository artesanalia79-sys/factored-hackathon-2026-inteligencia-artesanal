-- T6: share of transactions on card products (served by transactions_enriched) vs accounts and
-- loans (not served). Aggregates only.
select coalesce(p.card_type, 'not a card') as card_type, count(*) as transactions
from {{ ref('silver_transactions') }} as t
left join {{ ref('silver_products') }} as p using (product_id)
group by 1
order by 2 desc
