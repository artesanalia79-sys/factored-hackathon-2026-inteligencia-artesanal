{#- Question texts and open comments are not carried to silver (responses and sentiment are). -#}
{%- set casts = [
    ('survey_ts', 'timestamp'), ('process_date', 'date'), ('main_score', 'integer'),
    ('question_1_response', 'integer'), ('question_2_response', 'integer'),
    ('question_3_response', 'integer'), ('response_time_hours', 'decimal(8,2)'),
    ('campaign_response_rate', 'decimal(5,2)'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum',
                 'dq_orphan_interaction', 'dq_orphan_customer', 'dq_orphan_agent',
                 'dq_out_of_range', 'dq_late_arrival'] -%}

with source as (
    select * from {{ source('bronze', 'satisfaction_surveys') }}
),

renamed as (
    select
        {{ txt('survey_id') }} as survey_id,
        {{ txt('survey_date') }} as survey_ts,
        {{ txt('process_date') }} as process_date,
        {{ txt('interaction_id') }} as interaction_id,
        {{ txt('customer_id') }} as customer_id,
        {{ txt('agent_id') }} as agent_id,
        {{ txt('survey_type') }} as survey_type,
        {{ txt('send_channel') }} as send_channel,
        {{ txt('main_score') }} as main_score,
        {{ txt('nps_category') }} as nps_category,
        {{ txt('question_1_response') }} as question_1_response,
        {{ txt('question_2_response') }} as question_2_response,
        {{ txt('question_3_response') }} as question_3_response,
        {{ txt('comment_sentiment') }} as comment_sentiment,
        {{ txt('response_time_hours') }} as response_time_hours,
        {{ txt('campaign_response_rate') }} as campaign_response_rate,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.survey_id,
        {{ typed_columns(casts) }},
        r.interaction_id, r.customer_id, r.agent_id, r.survey_type, r.send_channel,
        r.nps_category, r.comment_sentiment,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        t.*,
        {{ any_null(['t.survey_ts', 't.process_date', 't.customer_id', 't.survey_type',
                     't.send_channel', 't.main_score']) }} as dq_missing_required,
        {{ invalid_enum([('t.survey_type', 'survey_type'), ('t.send_channel', 'survey_channel'),
                         ('t.nps_category', 'nps_category'),
                         ('t.comment_sentiment', 'comment_sentiment')]) }} as dq_invalid_enum,
        (t.interaction_id is not null and i.interaction_id is null) as dq_orphan_interaction,
        (t.customer_id is not null and c.customer_id is null) as dq_orphan_customer,
        (t.agent_id is not null and a.agent_id is null) as dq_orphan_agent,
        coalesce(
            (t.survey_type = 'NPS' and t.main_score not between 0 and 10)
            or (t.survey_type in ('CSAT', 'CES') and t.main_score not between 1 and 5)
            or t.question_1_response not between 1 and 5
            or t.question_2_response not between 1 and 5
            or t.question_3_response not between 1 and 5
            or t.response_time_hours < 0
            or t.campaign_response_rate not between 0 and 100,
            false
        ) as dq_out_of_range,
        coalesce(t.process_date > cast(t.survey_ts as date), false) as dq_late_arrival
    from typed as t
    left join (select interaction_id from {{ ref('silver_call_center_interactions') }}) as i
        on i.interaction_id = t.interaction_id
    left join (select customer_id from {{ ref('silver_customers') }}) as c
        on c.customer_id = t.customer_id
    left join (select agent_id from {{ ref('silver_service_agents') }}) as a
        on a.agent_id = t.agent_id
),

final as (
    {{ dedup_final('flagged', 'survey_id',
        ['survey_id', 'survey_ts', 'process_date', 'interaction_id', 'customer_id', 'agent_id',
         'survey_type', 'send_channel', 'main_score', 'nps_category', 'question_1_response',
         'question_2_response', 'question_3_response', 'comment_sentiment',
         'response_time_hours', 'campaign_response_rate'] + flags,
        flags, 'process_date desc nulls last') }}
)

select * from final
