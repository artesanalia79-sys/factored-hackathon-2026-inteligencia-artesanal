-- T16: status of central disputes at the snapshot date, with elapsed-time measures. Only
-- Resolved/Closed carry resolution_days. Aggregates only.
select
    status,
    count(*) as disputes,
    count(*) / sum(count(*)) over () as share,
    median(resolution_days) as median_resolution_days,
    median(date_diff('minute', creation_ts, assignment_ts) / 60.0) as median_assignment_hours,
    median(date_diff('minute', creation_ts, first_response_ts) / 60.0) as median_first_response_hours
from {{ ref('silver_complaints') }}
where subcategory = 'Cargo no reconocido'
group by 1
order by 2 desc
