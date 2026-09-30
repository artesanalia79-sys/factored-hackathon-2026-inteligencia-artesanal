-- T6: card currency by customer country and transaction currency by transaction country.
-- MXN never appears: Mexican cards and transactions are in USD. Aggregates only.
select 'card' as level, c.country_code as country, p.currency, count(*) as rows
from {{ ref('silver_products') }} as p
join {{ ref('silver_customers') }} as c using (customer_id)
where p.card_type is not null
group by 1, 2, 3
union all
select 'transaction', transaction_country_code, currency, count(*)
from {{ ref('silver_transactions') }}
group by 1, 2, 3
order by 1, 2, 4 desc
