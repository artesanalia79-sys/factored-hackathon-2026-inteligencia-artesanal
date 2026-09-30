{#- mentioned_entities (free-form extracted entities) is not carried to silver. -#}
{%- set casts = [
    ('process_date', 'date'), ('accent_confidence', 'decimal(3,2)'),
    ('duration_seconds', 'integer'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum',
                 'dq_orphan_interaction', 'dq_orphan_customer', 'dq_orphan_agent',
                 'dq_out_of_range'] -%}

with source as (
    select * from {{ source('bronze', 'call_transcripts') }}
),

renamed as (
    select
        {{ txt('transcript_id') }} as transcript_id,
        {{ txt('interaction_id') }} as interaction_id,
        {{ txt('process_date') }} as process_date,
        {{ txt('customer_id') }} as customer_id,
        {{ txt('agent_id') }} as agent_id,
        {{ txt('full_text') }} as full_text,
        {{ txt('customer_text') }} as customer_text,
        {{ txt('agent_text') }} as agent_text,
        {{ txt('detected_language') }} as detected_language,
        {{ txt('detected_accent') }} as detected_accent,
        {{ txt('accent_confidence') }} as accent_confidence,
        {{ txt('detected_keywords') }} as detected_keywords,
        {{ txt('detected_intents') }} as detected_intents,
        {{ txt('main_topics') }} as main_topics,
        {{ txt('transcription_model') }} as transcription_model,
        {{ txt('audio_quality') }} as audio_quality,
        {{ txt('duration_seconds') }} as duration_seconds,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.transcript_id, r.interaction_id,
        {{ typed_columns(casts) }},
        r.customer_id, r.agent_id, r.full_text, r.customer_text, r.agent_text,
        r.detected_language, r.detected_accent, r.detected_keywords, r.detected_intents,
        r.main_topics, r.transcription_model, r.audio_quality,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        t.*,
        {{ any_null(['t.interaction_id', 't.process_date', 't.customer_id', 't.agent_id',
                     't.full_text', 't.detected_language', 't.transcription_model',
                     't.duration_seconds']) }} as dq_missing_required,
        {{ invalid_enum([('t.detected_accent', 'accent'),
                         ('t.audio_quality', 'audio_quality')]) }} as dq_invalid_enum,
        (t.interaction_id is not null and i.interaction_id is null) as dq_orphan_interaction,
        (t.customer_id is not null and c.customer_id is null) as dq_orphan_customer,
        (t.agent_id is not null and a.agent_id is null) as dq_orphan_agent,
        coalesce(t.accent_confidence not between 0 and 1 or t.duration_seconds < 0, false)
            as dq_out_of_range
    from typed as t
    left join (select interaction_id from {{ ref('silver_call_center_interactions') }}) as i
        on i.interaction_id = t.interaction_id
    left join (select customer_id from {{ ref('silver_customers') }}) as c
        on c.customer_id = t.customer_id
    left join (select agent_id from {{ ref('silver_service_agents') }}) as a
        on a.agent_id = t.agent_id
),

final as (
    {{ dedup_final('flagged', 'transcript_id',
        ['transcript_id', 'interaction_id', 'process_date', 'customer_id', 'agent_id',
         'full_text', 'customer_text', 'agent_text', 'detected_language', 'detected_accent',
         'accent_confidence', 'detected_keywords', 'detected_intents', 'main_topics',
         'transcription_model', 'audio_quality', 'duration_seconds'] + flags,
        flags, 'process_date desc nulls last') }}
)

select * from final
