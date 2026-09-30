-- T16 complaints baseline by customer country, re-aggregated from the gold baseline. The broad
-- dispute definition (category Transactions) is the only one gold can express: it has no
-- subcategory. Resolution days are weighted by the complaints that carry them. Aggregates only.
select
    coalesce(country, '(null)') as country,
    sum(complaints) as complaints,
    sum(complaints) filter (where category = 'Transactions') as transactions_complaints,
    sum(avg_resolution_days * complaints_with_resolution_days)
        / nullif(sum(complaints_with_resolution_days), 0) as avg_resolution_days,
    sum(sla_breached_complaints) / nullif(sum(complaints_with_sla), 0) as sla_breach_rate
from {{ ref('gold_complaints_baseline') }}
group by 1
order by 2 desc
