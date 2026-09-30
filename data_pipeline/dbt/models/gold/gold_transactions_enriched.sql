{#- Serving table transactions_enriched: card transactions of served cards, with dq_flags.

    Incremental with a lookback window: each run reprocesses every process_date within
    `gold_lookback_days` of the newest one already loaded, so a row that arrives late (its
    process_date is older than rows already loaded) is still picked up when it falls inside the
    window. Older late rows need `--full-refresh`. The real source has 0 late arrivals
    (dq_late_arrival = 0); tests/gold proves the mechanism with a synthetic late row.

    is_fraud is never selected (ADR 0003, FORBIDDEN_COLUMNS). fraud_score is served as DOUBLE,
    for the policy engine only. -#}
{{ config(
    materialized='incremental',
    unique_key='transaction_id',
    incremental_strategy='delete+insert',
    on_schema_change='fail',
) }}

{#- Silver flags copied into dq_flags by name (dq_any and dq_duplicate_count are summaries). -#}
{%- set silver_flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum',
                        'dq_orphan_customer', 'dq_orphan_product', 'dq_orphan_branch',
                        'dq_product_customer_mismatch', 'dq_out_of_range', 'dq_late_arrival',
                        'dq_missing_fx_rate', 'dq_amount_usd_mismatch',
                        'dq_amount_usd_missing'] -%}
{#- Dispute-relevant flags computed here, named as in the synthetic fixture bank
    (src/bankagent/fixtures/builder.py `_dq_flags`) so both serving DBs share one vocabulary. -#}
{%- set gold_flags = ['dq_txn_before_product_opening', 'dq_txn_after_card_expiry',
                      'dq_missing_merchant', 'dq_possible_duplicate'] -%}
{%- set fixed_rates = var('amount_usd_fixed_rate') -%}

{%- if is_incremental() %}
{%- set lookback_start -%}
    (select max(process_date) - {{ var('gold_lookback_days') }} from {{ this }})
{%- endset %}
{%- endif %}

with transactions as (
    select * from {{ ref('silver_transactions') }}
    {%- if is_incremental() %}
    -- One extra day before the window so the duplicate check sees each row's predecessor
    -- (process_date is at most one day after the UTC event date).
    where process_date >= {{ lookback_start }} - 1
    {%- endif %}
),

cards as (
    select product_id, customer_id, opening_date, expiration_date
    from {{ ref('gold_customer_cards') }}
),

customers as (
    select customer_id, country from {{ ref('gold_customer_profile_min') }}
),

served as (
    -- Only transactions on a served card, by that card's owner, with every contract column.
    select
        t.*,
        k.opening_date as card_opening_date,
        k.expiration_date as card_expiration_date,
        c.country as customer_country
    from transactions as t
    inner join cards as k on k.product_id = t.product_id and k.customer_id = t.customer_id
    inner join customers as c on c.customer_id = t.customer_id
    where t.transaction_ts is not null
        and t.process_date is not null
        and t.transaction_type is not null
        and t.amount is not null
        and t.currency is not null
        and t.channel is not null
        and t.transaction_country_code is not null
        and t.transaction_status is not null
),

flagged as (
    select
        s.*,
        cast(s.transaction_ts as date) < s.card_opening_date as dq_txn_before_product_opening,
        coalesce(cast(s.transaction_ts as date) > s.card_expiration_date, false)
            as dq_txn_after_card_expiry,
        s.transaction_type = 'Purchase' and s.merchant_name is null as dq_missing_merchant,
        -- Consecutive transactions on the same card (ordered by ts, id) with the same merchant
        -- and amount within the window; NULL merchants compare equal, as in the fixture bank.
        coalesce(
            (
                lag(s.merchant_name) over w is not distinct from s.merchant_name
                and lag(s.amount) over w = s.amount
                and s.transaction_ts - lag(s.transaction_ts) over w
                    <= interval {{ var('gold_duplicate_window_seconds') }} second
            )
            or (
                lead(s.merchant_name) over w is not distinct from s.merchant_name
                and lead(s.amount) over w = s.amount
                and lead(s.transaction_ts) over w - s.transaction_ts
                    <= interval {{ var('gold_duplicate_window_seconds') }} second
            ),
            false
        ) as dq_possible_duplicate
    from served as s
    window w as (partition by s.product_id order by s.transaction_ts, s.transaction_id)
),

final as (
    select
        transaction_id,
        customer_id,
        product_id,
        transaction_ts,
        process_date,
        transaction_type,
        transaction_category,
        amount,
        currency,
        -- The source amount_usd uses a fixed rate per currency and is NULL for USD and for ~5%
        -- of ARS/COP rows: backfill with the same fixed rate (never the daily FX table) and with
        -- amount for USD. Other currencies without a source value stay NULL.
        coalesce(
            amount_usd,
            case currency
                when 'USD' then amount
                {%- for cur, rate in fixed_rates.items() %}
                when '{{ cur }}' then cast(round(amount / {{ rate }}, 2) as decimal(15,2))
                {%- endfor %}
            end
        ) as amount_usd,
        channel,
        merchant_name,
        merchant_category,
        transaction_country_code as transaction_country,
        transaction_city,
        transaction_status,
        cast(fraud_score as double) as fraud_score,
        transaction_country_code <> customer_country as is_foreign,
        {{ true_flag_names(silver_flags + gold_flags, 'f') }} as dq_flags
    from flagged as f
    {%- if is_incremental() %}
    where process_date >= {{ lookback_start }}
    {%- endif %}
)

select * from final
