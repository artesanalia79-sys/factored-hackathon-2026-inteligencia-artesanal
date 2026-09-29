{#- Grain (measured, see decision ledger): one row per (rate_date, source_currency,
    target_currency); 1,097 days x 12 ordered pairs of MXN/COP/ARS/USD = 13,164 rows. The
    dictionary's ~3,000 rows was an estimate. fx_rate_id is the single-column surrogate key. -#}
{%- set casts = [
    ('rate_date', 'date'), ('exchange_rate', 'decimal(12,6)'), ('buy_rate', 'decimal(12,6)'),
    ('sell_rate', 'decimal(12,6)'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum',
                 'dq_out_of_range'] -%}

with source as (
    select * from {{ source('bronze', 'daily_exchange_rates') }}
),

renamed as (
    select
        {{ txt('"date"') }} as rate_date,
        upper({{ txt('source_currency') }}) as source_currency,
        upper({{ txt('target_currency') }}) as target_currency,
        {{ txt('exchange_rate') }} as exchange_rate,
        {{ txt('buy_rate') }} as buy_rate,
        {{ txt('sell_rate') }} as sell_rate,
        {{ txt('source') }} as rate_source,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        {{ typed_columns(casts) }},
        r.source_currency, r.target_currency, r.rate_source,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        case
            when rate_date is not null and source_currency is not null
                and target_currency is not null
                then strftime(rate_date, '%Y-%m-%d') || '|' || source_currency || '|'
                    || target_currency
        end as fx_rate_id,
        *,
        {{ any_null(['rate_date', 'source_currency', 'target_currency', 'exchange_rate']) }}
            as dq_missing_required,
        {{ invalid_enum([('source_currency', 'currency'), ('target_currency', 'currency')]) }}
            as dq_invalid_enum,
        coalesce(
            exchange_rate <= 0 or buy_rate <= 0 or sell_rate <= 0
            or source_currency = target_currency,
            false
        ) as dq_out_of_range
    from typed
),

final as (
    {{ dedup_final('flagged', 'fx_rate_id',
        ['fx_rate_id', 'rate_date', 'source_currency', 'target_currency', 'exchange_rate',
         'buy_rate', 'sell_rate', 'rate_source'] + flags,
        flags, 'rate_source nulls last') }}
)

select * from final
