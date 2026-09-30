{%- set casts = [
    ('start_date', 'date'), ('end_date', 'date'), ('budget', 'decimal(12,2)'),
    ('expected_conversion_rate', 'decimal(5,2)'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum',
                 'dq_out_of_range'] -%}

with source as (
    select * from {{ source('bronze', 'marketing_campaigns') }}
),

renamed as (
    select
        {{ txt('campaign_id') }} as campaign_id,
        {{ txt('campaign_name') }} as campaign_name,
        {{ txt('campaign_type') }} as campaign_type,
        {{ txt('campaign_objective') }} as campaign_objective,
        {{ txt('promoted_product') }} as promoted_product,
        {{ txt('target_segment') }} as target_segment,
        {{ txt('target_country') }} as target_country,
        {{ txt('start_date') }} as start_date,
        {{ txt('end_date') }} as end_date,
        {{ txt('budget') }} as budget,
        {{ txt('campaign_status') }} as campaign_status,
        {{ txt('expected_conversion_rate') }} as expected_conversion_rate,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.campaign_id, r.campaign_name, r.campaign_type, r.campaign_objective,
        r.promoted_product, r.target_segment, r.target_country,
        {{ typed_columns(casts) }},
        r.campaign_status,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        *,
        {{ any_null(['campaign_name', 'campaign_type', 'campaign_objective', 'start_date',
                     'end_date', 'campaign_status']) }} as dq_missing_required,
        {{ invalid_enum([('campaign_type', 'campaign_type'),
                         ('campaign_objective', 'campaign_objective'),
                         ('target_segment', 'segment'), ('target_country', 'country'),
                         ('campaign_status', 'campaign_status')]) }} as dq_invalid_enum,
        coalesce(
            end_date < start_date or budget < 0
            or expected_conversion_rate not between 0 and 100,
            false
        ) as dq_out_of_range
    from typed
),

final as (
    {{ dedup_final('flagged', 'campaign_id',
        ['campaign_id', 'campaign_name', 'campaign_type', 'campaign_objective',
         'promoted_product', 'target_segment', 'target_country', 'start_date', 'end_date',
         'budget', 'campaign_status', 'expected_conversion_rate'] + flags,
        flags, 'start_date desc nulls last') }}
)

select * from final
