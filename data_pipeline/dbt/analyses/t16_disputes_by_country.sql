-- T16: central disputes by the complaining customer's country. The shares weight the per-country
-- labour cost into one cost per minute. Aggregates only.
select
    c.country_code as country,
    count(*) as disputes_central,
    count(*) / sum(count(*)) over () as share
from {{ ref('silver_complaints') }} as k
join {{ ref('silver_customers') }} as c on c.customer_id = k.customer_id
where k.subcategory = 'Cargo no reconocido'
group by 1
order by 2 desc
