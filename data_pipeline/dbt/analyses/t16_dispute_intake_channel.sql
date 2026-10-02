-- T16: how central disputes reach the bank (reception_channel) and how each channel performs.
-- The channel mix weights today's intake minutes per dispute. first_response_hours is measured
-- from creation_ts to first_response_ts. Aggregates only.
select
    coalesce(reception_channel, '(null)') as reception_channel,
    count(*) as disputes,
    count(*) / sum(count(*)) over () as share,
    count(*) filter (where status in ('Open', 'In Process')) / count(*) as open_share,
    median(resolution_days) as median_resolution_days,
    count(*) filter (where sla_breached) / nullif(count(sla_breached), 0) as sla_breach_rate,
    median(date_diff('minute', creation_ts, first_response_ts) / 60.0) as median_first_response_hours
from {{ ref('silver_complaints') }}
where subcategory = 'Cargo no reconocido'
group by 1
order by 2 desc
