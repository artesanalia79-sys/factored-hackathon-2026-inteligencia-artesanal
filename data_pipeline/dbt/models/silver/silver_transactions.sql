{#- is_fraud is kept here for OFFLINE analysis and evaluation labels only (ADR 0003); it is a
    FORBIDDEN_COLUMN and must never reach gold/serving. It is typed on its own and feeds no dq_*
    flag, so no label-derived bit can reach serving dq_flags. Latitude/longitude are dropped. -#}
{%- set casts = [
    ('transaction_ts', 'timestamp'), ('process_date', 'date'), ('amount', 'decimal(15,2)'),
    ('amount_usd', 'decimal(15,2)'), ('fraud_score', 'decimal(5,2)'),
] -%}
{#- Currencies whose source amount_usd uses a fixed rate (vars.amount_usd_fixed_rate). -#}
{%- set fixed_rates = var('amount_usd_fixed_rate') -%}
{%- set fixed_currencies -%}
    {%- for cur in fixed_rates %}'{{ cur }}'{{ ', ' if not loop.last }}{% endfor -%}
{%- endset -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum', 'dq_orphan_customer',
                 'dq_orphan_product', 'dq_orphan_branch', 'dq_product_customer_mismatch',
                 'dq_out_of_range', 'dq_late_arrival', 'dq_missing_fx_rate',
                 'dq_amount_usd_mismatch', 'dq_amount_usd_missing'] -%}

with source as (
    select * from {{ source('bronze', 'transactions') }}
),

renamed as (
    select
        {{ txt('transaction_id') }} as transaction_id,
        {{ txt('transaction_date') }} as transaction_ts,
        {{ txt('process_date') }} as process_date,
        {{ txt('product_id') }} as product_id,
        {{ txt('customer_id') }} as customer_id,
        {{ txt('transaction_type') }} as transaction_type,
        {{ txt('transaction_category') }} as transaction_category,
        {{ txt('amount') }} as amount,
        upper({{ txt('currency') }}) as currency,
        {{ txt('amount_usd') }} as amount_usd,
        {{ txt('channel') }} as channel,
        {{ txt('branch_id') }} as branch_id,
        {{ txt('merchant_name') }} as merchant_name,
        {{ txt('merchant_category') }} as merchant_category,
        {{ txt('transaction_country') }} as transaction_country,
        {{ txt('transaction_city') }} as transaction_city,
        {{ txt('transaction_status') }} as transaction_status,
        {{ txt('response_code') }} as response_code,
        {{ txt('is_fraud') }} as is_fraud,
        {{ txt('fraud_score') }} as fraud_score,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.transaction_id,
        {{ typed_columns(casts) }},
        -- offline-only label: typed separately, never part of any dq_* flag
        {{ typed('r.is_fraud', 'boolean') }} as is_fraud,
        r.product_id, r.customer_id, r.transaction_type, r.transaction_category, r.currency,
        r.channel, r.branch_id, r.merchant_name, r.merchant_category, r.transaction_country,
        {{ country_code('r.transaction_country') }} as transaction_country_code,
        r.transaction_city, r.transaction_status, r.response_code,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        t.*,
        case
            when t.currency = 'USD' then cast(1 as decimal(12,6))
            else fx.exchange_rate
        end as fx_rate_to_usd,
        {{ any_null(['t.transaction_ts', 't.process_date', 't.product_id', 't.customer_id',
                     't.transaction_type', 't.amount', 't.currency', 't.channel',
                     't.transaction_country', 't.transaction_status']) }}
            as dq_missing_required,
        {{ invalid_enum([('t.transaction_type', 'transaction_type'),
                         ('t.transaction_category', 'transaction_category'),
                         ('t.merchant_category', 'transaction_category'),
                         ('t.currency', 'currency'), ('t.channel', 'transaction_channel'),
                         ('t.transaction_status', 'transaction_status')]) }}
            as dq_invalid_enum,
        (t.customer_id is not null and c.customer_id is null) as dq_orphan_customer,
        (t.product_id is not null and p.product_id is null) as dq_orphan_product,
        (t.branch_id is not null and b.branch_id is null) as dq_orphan_branch,
        coalesce(p.customer_id <> t.customer_id, false) as dq_product_customer_mismatch,
        coalesce(
            t.amount <= 0 or t.amount_usd < 0 or t.fraud_score not between 0 and 100,
            false
        ) as dq_out_of_range,
        coalesce(t.process_date > cast(t.transaction_ts as date), false) as dq_late_arrival,
        coalesce(
            t.currency <> 'USD' and t.currency in ({{ enum_list('currency') }})
            and fx.exchange_rate is null,
            false
        ) as dq_missing_fx_rate,
        -- The source computes amount_usd with a fixed rate per currency (not the daily FX
        -- table), so the check re-derives it exactly: round(amount / fixed_rate, 2).
        coalesce(
            t.currency in ({{ fixed_currencies }}) and t.amount_usd is not null
            and abs(
                t.amount_usd
                - cast(round(t.amount / (case t.currency
                    {%- for cur, rate in fixed_rates.items() %}
                    when '{{ cur }}' then {{ rate }}
                    {%- endfor %}
                end), 2) as decimal(15,2))
            ) > cast({{ var('amount_usd_tolerance_usd') }} as decimal(15,2)),
            false
        ) as dq_amount_usd_mismatch,
        -- USD rows have NULL amount_usd by design (T6 fills them with amount): not flagged.
        coalesce(t.currency in ({{ fixed_currencies }}) and t.amount_usd is null, false)
            as dq_amount_usd_missing
    from typed as t
    left join (select customer_id from {{ ref('silver_customers') }}) as c
        on c.customer_id = t.customer_id
    left join (select product_id, customer_id from {{ ref('silver_products') }}) as p
        on p.product_id = t.product_id
    left join (select branch_id from {{ ref('silver_branches') }}) as b
        on b.branch_id = t.branch_id
    left join (
        select rate_date, source_currency, exchange_rate
        from {{ ref('silver_daily_exchange_rates') }}
        where target_currency = 'USD'
    ) as fx
        -- process_date is the batch date of a fixed 06:00 UTC cutoff (local midnight to 03:00 in
        -- MX/CO/AR), not the local business date; 25% of transactions have a UTC date one day
        -- later. Joining on it leaves no transaction without a rate (data_audit.md A1).
        on fx.rate_date = t.process_date
        and fx.source_currency = t.currency
),

final as (
    {{ dedup_final('flagged', 'transaction_id',
        ['transaction_id', 'transaction_ts', 'process_date', 'product_id', 'customer_id',
         'transaction_type', 'transaction_category', 'amount', 'currency', 'amount_usd',
         'fx_rate_to_usd', 'channel', 'branch_id', 'merchant_name', 'merchant_category',
         'transaction_country', 'transaction_country_code', 'transaction_city',
         'transaction_status', 'response_code', 'fraud_score', 'is_fraud'] + flags,
        flags, 'process_date desc nulls last, transaction_ts desc nulls last') }}
)

select * from final
