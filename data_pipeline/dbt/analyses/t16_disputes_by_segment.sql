-- T16: central disputes by customer segment (disparity check across authorized segments):
-- volume, backlog, resolution days and SLA breaches. Aggregates only.
select
    coalesce(c.segment, '(null)') as segment,
    count(*) as disputes,
    count(*) / sum(count(*)) over () as share,
    count(*) filter (where k.status in ('Open', 'In Process')) / count(*) as open_share,
    median(k.resolution_days) as median_resolution_days,
    count(*) filter (where k.sla_breached) / nullif(count(k.sla_breached), 0) as sla_breach_rate
from {{ ref('silver_complaints') }} as k
left join {{ ref('silver_customers') }} as c on c.customer_id = k.customer_id
where k.subcategory = 'Cargo no reconocido'
group by 1
order by 2 desc
