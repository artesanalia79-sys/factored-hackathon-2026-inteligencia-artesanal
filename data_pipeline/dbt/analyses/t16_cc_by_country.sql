-- T16 call-center baseline by customer country, re-aggregated from the gold baseline (sums, not
-- averages of averages). Aggregates only.
select
    coalesce(country, '(null)') as country,
    sum(contacts) as contacts,
    sum(total_duration_seconds) / nullif(sum(contacts_with_duration), 0) / 60 as avg_duration_min,
    sum(resolved_contacts) / sum(contacts) as fcr_rate,
    sum(escalated_contacts) / sum(contacts) as escalation_rate
from {{ ref('gold_cc_contact_baseline') }}
group by 1
order by 2 desc
