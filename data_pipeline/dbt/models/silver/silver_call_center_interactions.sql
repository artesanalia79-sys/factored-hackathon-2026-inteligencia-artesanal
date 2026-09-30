{%- set casts = [
    ('interaction_ts', 'timestamp'), ('process_date', 'date'), ('duration_seconds', 'integer'),
    ('wait_time_seconds', 'integer'), ('was_resolved', 'boolean'),
    ('requires_followup', 'boolean'), ('sentiment_score', 'decimal(3,2)'),
    ('was_escalated', 'boolean'), ('has_transcript', 'boolean'), ('has_recording', 'boolean'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum', 'dq_orphan_customer',
                 'dq_orphan_agent', 'dq_out_of_range', 'dq_late_arrival'] -%}

with source as (
    select * from {{ source('bronze', 'call_center_interactions') }}
),

renamed as (
    select
        {{ txt('interaction_id') }} as interaction_id,
        {{ txt('interaction_date') }} as interaction_ts,
        {{ txt('process_date') }} as process_date,
        {{ txt('customer_id') }} as customer_id,
        {{ txt('agent_id') }} as agent_id,
        {{ txt('interaction_type') }} as interaction_type,
        {{ txt('channel') }} as channel,
        {{ txt('contact_reason') }} as contact_reason,
        {{ txt('reason_category') }} as reason_category,
        {{ txt('duration_seconds') }} as duration_seconds,
        {{ txt('wait_time_seconds') }} as wait_time_seconds,
        {{ txt('was_resolved') }} as was_resolved,
        {{ txt('requires_followup') }} as requires_followup,
        {{ txt('detected_sentiment') }} as detected_sentiment,
        {{ txt('sentiment_score') }} as sentiment_score,
        {{ txt('customer_detected_accent') }} as customer_detected_accent,
        {{ txt('agent_used_accent') }} as agent_used_accent,
        {{ txt('was_escalated') }} as was_escalated,
        {{ txt('mentioned_products') }} as mentioned_products,
        {{ txt('has_transcript') }} as has_transcript,
        {{ txt('has_recording') }} as has_recording,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.interaction_id,
        {{ typed_columns(casts) }},
        r.customer_id, r.agent_id, r.interaction_type, r.channel, r.contact_reason,
        r.reason_category, r.detected_sentiment, r.customer_detected_accent,
        r.agent_used_accent, r.mentioned_products,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        t.*,
        {{ any_null(['t.interaction_ts', 't.process_date', 't.customer_id',
                     't.interaction_type', 't.channel', 't.contact_reason', 't.reason_category',
                     't.requires_followup', 't.was_escalated', 't.has_transcript',
                     't.has_recording']) }} as dq_missing_required,
        {{ invalid_enum([('t.interaction_type', 'interaction_type'),
                         ('t.channel', 'interaction_channel'),
                         ('t.reason_category', 'reason_category'),
                         ('t.detected_sentiment', 'sentiment'),
                         ('t.customer_detected_accent', 'accent'),
                         ('t.agent_used_accent', 'accent')]) }} as dq_invalid_enum,
        (t.customer_id is not null and c.customer_id is null) as dq_orphan_customer,
        (t.agent_id is not null and a.agent_id is null) as dq_orphan_agent,
        coalesce(
            t.duration_seconds < 0 or t.wait_time_seconds < 0
            or t.sentiment_score not between -1 and 1,
            false
        ) as dq_out_of_range,
        coalesce(t.process_date > cast(t.interaction_ts as date), false) as dq_late_arrival
    from typed as t
    left join (select customer_id from {{ ref('silver_customers') }}) as c
        on c.customer_id = t.customer_id
    left join (select agent_id from {{ ref('silver_service_agents') }}) as a
        on a.agent_id = t.agent_id
),

final as (
    {{ dedup_final('flagged', 'interaction_id',
        ['interaction_id', 'interaction_ts', 'process_date', 'customer_id', 'agent_id',
         'interaction_type', 'channel', 'contact_reason', 'reason_category',
         'duration_seconds', 'wait_time_seconds', 'was_resolved', 'requires_followup',
         'detected_sentiment', 'sentiment_score', 'customer_detected_accent',
         'agent_used_accent', 'was_escalated', 'mentioned_products', 'has_transcript',
         'has_recording'] + flags,
        flags, 'process_date desc nulls last') }}
)

select * from final
