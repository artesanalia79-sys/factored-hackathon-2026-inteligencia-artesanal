{#- Serving table _serving_metadata (key/value). as_of_date is the last process_date of silver
    transactions (2026-06-17 on the curated data), never max(last_updated): ~6% of last_updated
    values are in the future (data_audit.md B4). contract_version, data_mode and source are
    passed by the serving build (src/bankagent/gold/build.py); their "unset" defaults fail
    validate_serving_db on purpose. built_at is dbt's run start (UTC). -#}

with as_of as (
    select max(process_date) as as_of_date from {{ ref('silver_transactions') }}
),

final as (
    select 'data_mode' as key, '{{ var("serving_data_mode") }}' as value
    union all
    select 'as_of_date', strftime(as_of_date, '%Y-%m-%d') from as_of
    union all
    select 'built_at', '{{ run_started_at.strftime("%Y-%m-%dT%H:%M:%SZ") }}'
    union all
    select 'source', '{{ var("serving_source") | replace("'", "''") }}'
    union all
    select 'contract_version', '{{ var("serving_contract_version") }}'
)

select * from final
