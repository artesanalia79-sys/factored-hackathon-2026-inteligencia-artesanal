-- Transactions against the lifecycle of their product and customer: before product opening,
-- before customer registration, after card expiry, on inactive/closed customers, on non-active
-- products. Dates are compared on process_date. Product-level facts: product_status_activity.sql.
-- Aggregates only.
select
    count(*) as transactions,
    count(*) filter (where t.process_date < p.opening_date) as before_product_opening,
    count(*) filter (where t.process_date < c.registration_date) as before_customer_registration,
    count(*) filter (where p.expiration_date is not null) as on_product_with_expiry,
    count(*) filter (where t.process_date > p.expiration_date) as after_card_expiry,
    count(*) filter (where c.customer_status in ('Inactive', 'Closed')) as customer_inactive_or_closed,
    count(*) filter (where p.product_status <> 'Active') as on_product_not_active
from {{ ref('silver_transactions') }} as t
join {{ ref('silver_products') }} as p on p.product_id = t.product_id
join {{ ref('silver_customers') }} as c on c.customer_id = t.customer_id
