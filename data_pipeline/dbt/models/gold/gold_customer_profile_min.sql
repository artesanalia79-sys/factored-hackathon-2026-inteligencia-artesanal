{#- Serving table customer_profile_min. One row per silver customer whose contract columns are
    all present; a customer missing one is not served (counted by the serving build). No contact
    or identity data: document_number is only present as silver's document_hash. -#}

with customers as (
    select * from {{ ref('silver_customers') }}
),

final as (
    select
        customer_id,
        document_hash,
        first_name,
        country_code as country,
        segment,
        customer_status,
        -- Placeholder: the source has no language and all its text is Spanish (decision ledger).
        '{{ var("gold_default_preferred_language") }}' as preferred_language,
        registration_date
    from customers
    where document_hash is not null
        and first_name is not null
        and country_code is not null
        and segment is not null
        and customer_status is not null
        and registration_date is not null
)

select * from final
