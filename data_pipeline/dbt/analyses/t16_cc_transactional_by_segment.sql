-- T16: Transactional contacts by customer segment (disparity check across authorized segments).
-- Aggregates only.
select
    coalesce(c.segment, '(null)') as segment,
    count(*) as contacts,
    avg(i.duration_seconds) / 60 as avg_duration_min,
    avg(i.wait_time_seconds) / 60 as avg_wait_min,
    count(*) filter (where i.was_resolved) / count(*) as fcr_rate,
    count(*) filter (where i.was_escalated) / count(*) as escalation_rate
from {{ ref('silver_call_center_interactions') }} as i
left join {{ ref('silver_customers') }} as c on c.customer_id = i.customer_id
where i.reason_category = 'Transaccional'
group by 1
order by 2 desc
