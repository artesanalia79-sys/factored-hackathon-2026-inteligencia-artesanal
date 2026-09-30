-- T6: is_foreign = transaction country <> customer country (residence). Share of each customer
-- country's transactions by transaction country. Aggregates only.
select
  c.country_code as customer_country,
  t.transaction_country_code as transaction_country,
  count(*) as transactions,
  round(100.0 * count(*) / sum(count(*)) over (partition by c.country_code), 1) as pct
from {{ ref('silver_transactions') }} as t
join {{ ref('silver_customers') }} as c using (customer_id)
group by 1, 2
order by 1, 3 desc
