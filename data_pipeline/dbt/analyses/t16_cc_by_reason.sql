-- T16 call-center baseline by contact reason (contact_reason equals reason_category in the
-- source; 6 values, no dispute label). Durations and waits are averaged over the contacts that
-- carry them (the *_with_* counts). was_resolved is the source's first-contact-resolution flag.
-- Aggregates only.
select
    reason_category,
    count(*) as contacts,
    count(duration_seconds) as contacts_with_duration,
    avg(duration_seconds) / 60 as avg_duration_min,
    median(duration_seconds) / 60 as median_duration_min,
    count(wait_time_seconds) as contacts_with_wait,
    avg(wait_time_seconds) / 60 as avg_wait_min,
    count(*) filter (where was_resolved) / count(*) as fcr_rate,
    count(*) filter (where requires_followup) / count(*) as followup_rate,
    count(*) filter (where was_escalated) / count(*) as escalation_rate
from {{ ref('silver_call_center_interactions') }}
group by 1
order by 2 desc
