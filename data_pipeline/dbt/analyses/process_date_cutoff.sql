-- Hours between process_date (midnight) and the UTC event timestamp, per fact table and customer
-- country. A fixed batch cutoff gives the same [min_hours, max_hours] window in every country; a
-- local business date would shift with the country's UTC offset (MX -6, CO -5, AR -3).
-- off_rule_6h / off_rule_8h count rows where process_date <> date(ts - 6h / 8h). Aggregates only.
with events as (
    select 'transactions' as source_table, transaction_ts as ts, process_date, customer_id
    from {{ ref('silver_transactions') }}
    union all
    select 'call_center_interactions', interaction_ts, process_date, customer_id
    from {{ ref('silver_call_center_interactions') }}
    union all
    select 'complaints', creation_ts, process_date, customer_id
    from {{ ref('silver_complaints') }}
    union all
    select 'digital_events', event_ts, process_date, customer_id
    from {{ ref('silver_digital_events') }}
    union all
    select 'campaign_sends', send_ts, process_date, customer_id
    from {{ ref('silver_campaign_sends') }}
)
select
    e.source_table,
    c.country_code,
    count(*) as rows,
    round(min(epoch(e.ts - cast(e.process_date as timestamp))) / 3600, 2) as min_hours,
    round(max(epoch(e.ts - cast(e.process_date as timestamp))) / 3600, 2) as max_hours,
    count(*) filter (where cast(e.ts - interval 6 hour as date) <> e.process_date) as off_rule_6h,
    count(*) filter (where cast(e.ts - interval 8 hour as date) <> e.process_date) as off_rule_8h
from events as e
left join {{ ref('silver_customers') }} as c on c.customer_id = e.customer_id
group by all
order by 1, 2
