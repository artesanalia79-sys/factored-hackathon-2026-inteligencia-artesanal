{#- Sensitive: product_number (full card/account number) is reduced to card_last4 here. -#}
{%- set casts = [
    ('current_balance', 'decimal(15,2)'), ('credit_limit', 'decimal(15,2)'),
    ('interest_rate', 'decimal(5,2)'), ('opening_date', 'date'), ('expiration_date', 'date'),
    ('has_linked_app', 'boolean'), ('days_past_due', 'integer'),
    ('last_transaction_ts', 'timestamp'), ('last_updated_ts', 'timestamp'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum', 'dq_orphan_customer',
                 'dq_orphan_branch', 'dq_out_of_range', 'dq_product_number_duplicate'] -%}

with source as (
    select * from {{ source('bronze', 'products') }}
),

renamed as (
    select
        {{ txt('product_id') }} as product_id,
        {{ txt('customer_id') }} as customer_id,
        {{ txt('product_type') }} as product_type,
        {{ txt('product_number') }} as product_number,
        upper({{ txt('currency') }}) as currency,
        {{ txt('current_balance') }} as current_balance,
        {{ txt('credit_limit') }} as credit_limit,
        {{ txt('interest_rate') }} as interest_rate,
        {{ txt('opening_date') }} as opening_date,
        {{ txt('expiration_date') }} as expiration_date,
        {{ txt('opening_branch_id') }} as opening_branch_id,
        {{ txt('product_status') }} as product_status,
        {{ txt('opening_channel') }} as opening_channel,
        {{ txt('has_linked_app') }} as has_linked_app,
        {{ txt('days_past_due') }} as days_past_due,
        {{ txt('last_transaction_date') }} as last_transaction_ts,
        {{ txt('last_updated') }} as last_updated_ts,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.product_id, r.customer_id, r.product_type,
        case
            when strip_accents(lower(r.product_type)) in ('tarjeta credito', 'credit card')
                then 'credit'
            when strip_accents(lower(r.product_type)) in ('tarjeta debito', 'debit card')
                then 'debit'
        end as card_type,
        right(regexp_replace(r.product_number, '[^0-9]', '', 'g'), 4) as card_last4,
        r.currency,
        {{ typed_columns(casts) }},
        r.opening_branch_id, r.product_status, r.opening_channel,
        r.product_number is null as _product_number_missing,
        -- distinct owners of the number, so a duplicated bronze row of the same product
        -- (dq_duplicate_count) is not a collision
        coalesce(
            r.product_number is not null
            and count(distinct r.product_id) over (partition by r.product_number) > 1,
            false
        ) as dq_product_number_duplicate,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        t.*,
        (
            {{ any_null(['t.customer_id', 't.product_type', 't.currency', 't.current_balance',
                         't.opening_date', 't.opening_branch_id', 't.product_status',
                         't.opening_channel', 't.has_linked_app', 't.last_updated_ts']) }}
            or t._product_number_missing
        ) as dq_missing_required,
        {{ invalid_enum([('t.product_type', 'product_type'), ('t.currency', 'currency'),
                         ('t.product_status', 'product_status'),
                         ('t.opening_channel', 'opening_channel')]) }} as dq_invalid_enum,
        (t.customer_id is not null and c.customer_id is null) as dq_orphan_customer,
        (t.opening_branch_id is not null and b.branch_id is null) as dq_orphan_branch,
        coalesce(
            t.credit_limit < 0
            or t.interest_rate not between 0 and 100
            or t.days_past_due < 0
            or t.expiration_date < t.opening_date,
            false
        ) as dq_out_of_range
    from typed as t
    left join (select customer_id from {{ ref('silver_customers') }}) as c
        on c.customer_id = t.customer_id
    left join (select branch_id from {{ ref('silver_branches') }}) as b
        on b.branch_id = t.opening_branch_id
),

final as (
    {{ dedup_final('flagged', 'product_id',
        ['product_id', 'customer_id', 'product_type', 'card_type', 'card_last4', 'currency',
         'current_balance', 'credit_limit', 'interest_rate', 'opening_date', 'expiration_date',
         'opening_branch_id', 'product_status', 'opening_channel', 'has_linked_app',
         'days_past_due', 'last_transaction_ts', 'last_updated_ts'] + flags,
        flags, 'last_updated_ts desc nulls last') }}
)

select * from final
