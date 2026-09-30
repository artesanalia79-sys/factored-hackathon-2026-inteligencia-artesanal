-- T16: Transactional contacts (the closest call-center reason to disputes) by channel. Duration
-- exists only for Phone, App and Web, wait time only for Phone. Aggregates only.
select
    channel,
    count(*) as contacts,
    count(duration_seconds) as contacts_with_duration,
    avg(duration_seconds) / 60 as avg_duration_min,
    count(wait_time_seconds) as contacts_with_wait,
    avg(wait_time_seconds) / 60 as avg_wait_min,
    count(*) filter (where was_resolved) / count(*) as fcr_rate,
    count(*) filter (where was_escalated) / count(*) as escalation_rate
from {{ ref('silver_call_center_interactions') }}
where reason_category = 'Transaccional'
group by 1
order by 2 desc
