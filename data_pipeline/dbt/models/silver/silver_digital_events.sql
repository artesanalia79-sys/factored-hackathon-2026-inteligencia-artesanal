{#- ip_address is read only to keep bronze faithful; it never leaves bronze. -#}
{%- set casts = [
    ('event_ts', 'timestamp'), ('process_date', 'date'), ('event_value', 'decimal(15,2)'),
    ('duration_seconds', 'integer'), ('is_mobile', 'boolean'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum', 'dq_orphan_customer',
                 'dq_orphan_product', 'dq_out_of_range', 'dq_late_arrival'] -%}

with source as (
    select * from {{ source('bronze', 'digital_events') }}
),

renamed as (
    select
        {{ txt('event_id') }} as event_id,
        {{ txt('event_date') }} as event_ts,
        {{ txt('process_date') }} as process_date,
        {{ txt('customer_id') }} as customer_id,
        {{ txt('session_id') }} as session_id,
        {{ txt('event_type') }} as event_type,
        {{ txt('event_category') }} as event_category,
        {{ txt('channel') }} as channel,
        {{ txt('platform') }} as platform,
        {{ txt('browser') }} as browser,
        {{ txt('app_version') }} as app_version,
        {{ txt('page_url') }} as page_url,
        {{ txt('page_title') }} as page_title,
        {{ txt('action') }} as action,
        {{ txt('element_id') }} as element_id,
        {{ txt('product_id') }} as product_id,
        {{ txt('event_value') }} as event_value,
        {{ txt('duration_seconds') }} as duration_seconds,
        {{ txt('ip_country') }} as ip_country,
        {{ txt('ip_city') }} as ip_city,
        {{ txt('is_mobile') }} as is_mobile,
        {{ txt('referrer') }} as referrer,
        {{ txt('utm_source') }} as utm_source,
        {{ txt('utm_medium') }} as utm_medium,
        {{ txt('utm_campaign') }} as utm_campaign,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.event_id,
        {{ typed_columns(casts) }},
        r.customer_id, r.session_id, r.event_type, r.event_category, r.channel, r.platform,
        r.browser, r.app_version, r.page_url, r.page_title, r.action, r.element_id,
        r.product_id, r.ip_country,
        {{ country_code('r.ip_country') }} as ip_country_code,
        r.ip_city, r.referrer, r.utm_source, r.utm_medium, r.utm_campaign,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        t.*,
        {{ any_null(['t.event_ts', 't.process_date', 't.session_id', 't.event_type',
                     't.event_category', 't.channel', 't.is_mobile']) }} as dq_missing_required,
        {{ invalid_enum([('t.event_type', 'event_type'), ('t.event_category', 'event_category'),
                         ('t.channel', 'digital_channel'), ('t.platform', 'platform')]) }}
            as dq_invalid_enum,
        (t.customer_id is not null and c.customer_id is null) as dq_orphan_customer,
        (t.product_id is not null and p.product_id is null) as dq_orphan_product,
        coalesce(t.event_value < 0 or t.duration_seconds < 0, false) as dq_out_of_range,
        coalesce(t.process_date > cast(t.event_ts as date), false) as dq_late_arrival
    from typed as t
    left join (select customer_id from {{ ref('silver_customers') }}) as c
        on c.customer_id = t.customer_id
    left join (select product_id from {{ ref('silver_products') }}) as p
        on p.product_id = t.product_id
),

final as (
    {{ dedup_final('flagged', 'event_id',
        ['event_id', 'event_ts', 'process_date', 'customer_id', 'session_id', 'event_type',
         'event_category', 'channel', 'platform', 'browser', 'app_version', 'page_url',
         'page_title', 'action', 'element_id', 'product_id', 'event_value', 'duration_seconds',
         'ip_country', 'ip_country_code', 'ip_city', 'is_mobile', 'referrer', 'utm_source',
         'utm_medium', 'utm_campaign'] + flags,
        flags, 'process_date desc nulls last') }}
)

select * from final
