{%- set casts = [
    ('send_ts', 'timestamp'), ('process_date', 'date'), ('was_delivered', 'boolean'),
    ('was_opened', 'boolean'), ('open_ts', 'timestamp'), ('was_clicked', 'boolean'),
    ('click_ts', 'timestamp'), ('click_count', 'integer'), ('had_conversion', 'boolean'),
    ('conversion_ts', 'timestamp'), ('conversion_value', 'decimal(15,2)'),
    ('send_cost', 'decimal(10,4)'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum', 'dq_orphan_campaign',
                 'dq_orphan_customer', 'dq_out_of_range', 'dq_late_arrival'] -%}

with source as (
    select * from {{ source('bronze', 'campaign_sends') }}
),

renamed as (
    select
        {{ txt('send_id') }} as send_id,
        {{ txt('send_date') }} as send_ts,
        {{ txt('process_date') }} as process_date,
        {{ txt('campaign_id') }} as campaign_id,
        {{ txt('customer_id') }} as customer_id,
        {{ txt('send_channel') }} as send_channel,
        {{ txt('template_used') }} as template_used,
        {{ txt('subject') }} as subject,
        {{ txt('send_status') }} as send_status,
        {{ txt('was_delivered') }} as was_delivered,
        {{ txt('was_opened') }} as was_opened,
        {{ txt('open_date') }} as open_ts,
        {{ txt('was_clicked') }} as was_clicked,
        {{ txt('click_date') }} as click_ts,
        {{ txt('click_count') }} as click_count,
        {{ txt('had_conversion') }} as had_conversion,
        {{ txt('conversion_date') }} as conversion_ts,
        {{ txt('conversion_value') }} as conversion_value,
        {{ txt('open_device') }} as open_device,
        {{ txt('open_country') }} as open_country,
        {{ txt('failure_reason') }} as failure_reason,
        {{ txt('send_cost') }} as send_cost,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.send_id,
        {{ typed_columns(casts) }},
        r.campaign_id, r.customer_id, r.send_channel, r.template_used, r.subject,
        r.send_status, r.open_device, r.open_country, r.failure_reason,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        t.*,
        {{ any_null(['t.send_ts', 't.process_date', 't.campaign_id', 't.customer_id',
                     't.send_channel', 't.send_status', 't.was_delivered',
                     't.had_conversion']) }} as dq_missing_required,
        {{ invalid_enum([('t.send_channel', 'send_channel'),
                         ('t.send_status', 'send_status')]) }} as dq_invalid_enum,
        (t.campaign_id is not null and m.campaign_id is null) as dq_orphan_campaign,
        (t.customer_id is not null and c.customer_id is null) as dq_orphan_customer,
        coalesce(
            t.open_ts < t.send_ts or t.click_ts < t.send_ts or t.conversion_ts < t.send_ts
            or t.click_count < 0 or t.conversion_value < 0 or t.send_cost < 0,
            false
        ) as dq_out_of_range,
        coalesce(t.process_date > cast(t.send_ts as date), false) as dq_late_arrival
    from typed as t
    left join (select campaign_id from {{ ref('silver_marketing_campaigns') }}) as m
        on m.campaign_id = t.campaign_id
    left join (select customer_id from {{ ref('silver_customers') }}) as c
        on c.customer_id = t.customer_id
),

final as (
    {{ dedup_final('flagged', 'send_id',
        ['send_id', 'send_ts', 'process_date', 'campaign_id', 'customer_id', 'send_channel',
         'template_used', 'subject', 'send_status', 'was_delivered', 'was_opened', 'open_ts',
         'was_clicked', 'click_ts', 'click_count', 'had_conversion', 'conversion_ts',
         'conversion_value', 'open_device', 'open_country', 'failure_reason',
         'send_cost'] + flags,
        flags, 'process_date desc nulls last') }}
)

select * from final
