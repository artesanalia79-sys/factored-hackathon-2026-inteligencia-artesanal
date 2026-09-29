-- process_date minus the UTC date of the event, per fact table. Aggregates only.
-- A lag of -1 is the local-vs-UTC day boundary (source timestamps are UTC, process_date is the
-- local business date); a lag > 0 would be a late arrival.
select 'transactions' as source_table, datediff('day', cast(transaction_ts as date), process_date) as lag_days, count(*) as rows
from {{ ref('silver_transactions') }} group by 1, 2
union all
select 'call_center_interactions', datediff('day', cast(interaction_ts as date), process_date), count(*)
from {{ ref('silver_call_center_interactions') }} group by 1, 2
union all
select 'satisfaction_surveys', datediff('day', cast(survey_ts as date), process_date), count(*)
from {{ ref('silver_satisfaction_surveys') }} group by 1, 2
union all
select 'digital_events', datediff('day', cast(event_ts as date), process_date), count(*)
from {{ ref('silver_digital_events') }} group by 1, 2
union all
select 'complaints', datediff('day', cast(creation_ts as date), process_date), count(*)
from {{ ref('silver_complaints') }} group by 1, 2
union all
select 'campaign_sends', datediff('day', cast(send_ts as date), process_date), count(*)
from {{ ref('silver_campaign_sends') }} group by 1, 2
order by 1, 2
