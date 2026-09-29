{#- Sensitive: document_number is replaced by document_hash; contact data, address, date of birth,
    credit score and income are read only to flag defects and never leave this model. -#}
{%- set casts = [
    ('registration_ts', 'timestamp'), ('last_updated_ts', 'timestamp'),
    ('accepts_marketing', 'boolean'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum', 'dq_orphan_branch',
                 'dq_out_of_range'] -%}

{#- dq_orphan_branch stays a column but is left out of dq_any: the source branch FK is a random
    token on nearly every row (decision ledger 2026-09-29), so folding it in would flag the whole
    table and make dq_any useless downstream (T6). -#}
{%- set dq_any_flags = flags | reject('equalto', 'dq_orphan_branch') | list -%}

with source as (
    select * from {{ source('bronze', 'customers') }}
),

renamed as (
    select
        {{ txt('customer_id') }} as customer_id,
        {{ txt('document_number') }} as document_number,
        {{ txt('document_type') }} as document_type,
        {{ txt('first_name') }} as first_name,
        {{ txt('last_name') }} as last_name,
        {{ txt('date_of_birth') }} as date_of_birth,
        {{ txt('gender') }} as gender,
        {{ txt('city') }} as city,
        {{ txt('state') }} as state,
        {{ txt('country') }} as country,
        {{ txt('detected_accent') }} as detected_accent,
        {{ txt('segment') }} as segment,
        {{ txt('credit_score') }} as credit_score,
        {{ txt('estimated_monthly_income') }} as estimated_monthly_income,
        {{ txt('registration_date') }} as registration_ts,
        {{ txt('registration_branch_id') }} as registration_branch_id,
        {{ txt('customer_status') }} as customer_status,
        {{ txt('last_updated') }} as last_updated_ts,
        {{ txt('accepts_marketing') }} as accepts_marketing,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.customer_id,
        sha256(r.document_number) as document_hash,
        r.document_type, r.first_name, r.gender, r.city, r.state, r.country,
        {{ country_code('r.country') }} as country_code,
        r.detected_accent, r.segment,
        {{ typed_columns(casts) }},
        cast({{ typed('r.registration_ts', 'timestamp') }} as date) as registration_date,
        r.registration_branch_id, r.customer_status,
        -- read for flags only (forbidden downstream)
        r.last_name is null as _last_name_missing,
        {{ typed('r.date_of_birth', 'date') }} as _date_of_birth,
        {{ typed('r.credit_score', 'integer') }} as _credit_score,
        {{ typed('r.estimated_monthly_income', 'decimal(12,2)') }} as _income,
        (
            {{ cast_failed(casts + [('date_of_birth', 'date'), ('credit_score', 'integer'),
                                    ('estimated_monthly_income', 'decimal(12,2)')]) }}
        ) as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        t.*,
        (
            {{ any_null(['t.document_hash', 't.document_type', 't.first_name', 't._date_of_birth',
                         't.city', 't.state', 't.country', 't.segment', 't.registration_ts',
                         't.registration_branch_id', 't.customer_status', 't.last_updated_ts',
                         't.accepts_marketing']) }}
            or t._last_name_missing
        ) as dq_missing_required,
        {{ invalid_enum([('t.document_type', 'document_type'), ('t.gender', 'gender'),
                         ('t.country', 'country'), ('t.detected_accent', 'accent'),
                         ('t.segment', 'segment'), ('t.customer_status', 'customer_status')]) }}
            as dq_invalid_enum,
        (t.registration_branch_id is not null and b.branch_id is null) as dq_orphan_branch,
        coalesce(
            t._credit_score not between 300 and 850
            or t._income < 0
            or t._date_of_birth > cast(t.registration_ts as date)
            or t.last_updated_ts < t.registration_ts,
            false
        ) as dq_out_of_range
    from typed as t
    left join (select branch_id from {{ ref('silver_branches') }}) as b
        on b.branch_id = t.registration_branch_id
),

final as (
    {{ dedup_final('flagged', 'customer_id',
        ['customer_id', 'document_hash', 'document_type', 'first_name', 'gender', 'city', 'state',
         'country', 'country_code', 'detected_accent', 'segment', 'registration_ts',
         'registration_date', 'registration_branch_id', 'customer_status', 'last_updated_ts',
         'accepts_marketing'] + flags,
        dq_any_flags, 'last_updated_ts desc nulls last') }}
)

select * from final
