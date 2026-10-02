-- T16 model input: measured handle time of a Transactional phone contact, used as the talk time
-- of a dispute received through the call center. Aggregates only.
select
    count(*) as contacts,
    count(duration_seconds) as contacts_with_duration,
    avg(duration_seconds) / 60 as avg_duration_min,
    median(duration_seconds) / 60 as median_duration_min
from {{ ref('silver_call_center_interactions') }}
where reason_category = 'Transaccional' and channel = 'Phone'
