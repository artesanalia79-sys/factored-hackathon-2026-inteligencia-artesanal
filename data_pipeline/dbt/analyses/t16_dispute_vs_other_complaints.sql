-- T16: do central disputes behave differently from the other complaints? Same resolution days,
-- SLA breach and repeat-complainer rates would mean the source does not model dispute-specific
-- handling. Aggregates only.
select
    subcategory is not distinct from 'Cargo no reconocido' as is_dispute,
    count(*) as complaints,
    avg(resolution_days) as avg_resolution_days,
    count(*) filter (where sla_breached) / nullif(count(sla_breached), 0) as sla_breach_rate,
    count(*) filter (where is_repeat_complainer) / count(*) as repeat_complainer_share,
    count(*) filter (where status in ('Open', 'In Process')) / count(*) as open_share
from {{ ref('silver_complaints') }}
group by 1
order by 1
