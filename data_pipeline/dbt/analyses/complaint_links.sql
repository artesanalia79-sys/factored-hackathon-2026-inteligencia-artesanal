-- Links from complaints to the rest of the model: does affected_product_id belong to the
-- complaining customer, is origin_interaction_id filled, and are claimed_amount and currency
-- consistent (one without the other). Aggregates only.
select
    count(*) as complaints,
    count(k.affected_product_id) as with_affected_product,
    count(*) filter (where p.customer_id = k.customer_id) as product_of_same_customer,
    count(*) filter (where p.customer_id <> k.customer_id) as product_of_other_customer,
    count(*) filter (
        where k.affected_product_id is not null and p.product_id is null
    ) as product_missing,
    count(k.origin_interaction_id) as with_origin_interaction,
    count(*) filter (
        where k.claimed_amount is not null and k.currency is null
    ) as amount_without_currency,
    count(*) filter (
        where k.claimed_amount is null and k.currency is not null
    ) as currency_without_amount
from {{ ref('silver_complaints') }} as k
left join {{ ref('silver_products') }} as p on p.product_id = k.affected_product_id
