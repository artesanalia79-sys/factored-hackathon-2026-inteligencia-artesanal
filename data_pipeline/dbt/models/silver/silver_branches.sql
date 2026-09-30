{%- set casts = [
    ('opening_time', 'time'), ('closing_time', 'time'),
    ('has_atms', 'boolean'), ('atm_count', 'integer'),
    ('has_teller_windows', 'boolean'), ('teller_window_count', 'integer'),
    ('branch_opening_date', 'date'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum', 'dq_out_of_range'] -%}

with source as (
    select * from {{ source('bronze', 'branches') }}
),

renamed as (
    select
        {{ txt('branch_id') }} as branch_id,
        {{ txt('branch_code') }} as branch_code,
        {{ txt('branch_name') }} as branch_name,
        {{ txt('branch_type') }} as branch_type,
        {{ txt('city') }} as city,
        {{ txt('state') }} as state,
        {{ txt('country') }} as country,
        {{ txt('geographic_zone') }} as geographic_zone,
        {{ txt('opening_time') }} as opening_time,
        {{ txt('closing_time') }} as closing_time,
        {{ txt('has_atms') }} as has_atms,
        {{ txt('atm_count') }} as atm_count,
        {{ txt('has_teller_windows') }} as has_teller_windows,
        {{ txt('teller_window_count') }} as teller_window_count,
        {{ txt('branch_opening_date') }} as branch_opening_date,
        {{ txt('branch_status') }} as branch_status,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.branch_id, r.branch_code, r.branch_name, r.branch_type, r.city, r.state, r.country,
        {{ country_code('r.country') }} as country_code,
        r.geographic_zone,
        {{ typed_columns(casts) }},
        r.branch_status,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        *,
        {{ any_null(['branch_code', 'branch_name', 'branch_type', 'city', 'state', 'country',
                     'geographic_zone', 'opening_time', 'closing_time', 'has_atms',
                     'has_teller_windows', 'branch_opening_date', 'branch_status']) }}
            as dq_missing_required,
        {{ invalid_enum([('branch_type', 'branch_type'), ('geographic_zone', 'geographic_zone'),
                         ('branch_status', 'branch_status'), ('country', 'country')]) }}
            as dq_invalid_enum,
        coalesce(closing_time <= opening_time or atm_count < 0 or teller_window_count < 0, false)
            as dq_out_of_range
    from typed
),

final as (
    {{ dedup_final('flagged', 'branch_id',
        ['branch_id', 'branch_code', 'branch_name', 'branch_type', 'city', 'state', 'country',
         'country_code', 'geographic_zone', 'opening_time', 'closing_time', 'has_atms',
         'atm_count', 'has_teller_windows', 'teller_window_count', 'branch_opening_date',
         'branch_status'] + flags,
        flags, 'branch_opening_date desc nulls last') }}
)

select * from final
