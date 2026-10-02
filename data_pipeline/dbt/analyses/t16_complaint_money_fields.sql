-- T16: can claimed_amount / compensation_granted size the money at stake in disputes? Shows how
-- often currency is missing and whether the amount scale depends on the currency (it should:
-- 1 USD is ~18 MXN, ~1,500 ARS, ~3,300 COP). Aggregates only.
select
    coalesce(currency, '(null)') as currency,
    count(*) as disputes,
    count(claimed_amount) as with_claimed_amount,
    median(claimed_amount) as median_claimed_amount,
    count(compensation_granted) as with_compensation
from {{ ref('silver_complaints') }}
where subcategory = 'Cargo no reconocido'
group by 1
order by 2 desc
