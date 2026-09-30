{#- Agent names, email and phone are not carried to silver (not needed for routing). -#}
{%- set casts = [
    ('hire_date', 'date'), ('avg_csat', 'decimal(3,2)'),
    ('total_monthly_interactions', 'integer'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum', 'dq_orphan_branch',
                 'dq_out_of_range', 'dq_employee_code_duplicate'] -%}

{#- dq_orphan_branch stays a column but is left out of dq_any: the source branch FK is a random
    token on nearly every row (decision ledger 2026-09-29), so folding it in would flag the whole
    table and make dq_any useless downstream (T6). -#}
{%- set dq_any_flags = flags | reject('equalto', 'dq_orphan_branch') | list -%}

with source as (
    select * from {{ source('bronze', 'service_agents') }}
),

renamed as (
    select
        {{ txt('agent_id') }} as agent_id,
        {{ txt('employee_code') }} as employee_code,
        {{ txt('first_name') }} as first_name,
        {{ txt('last_name') }} as last_name,
        {{ txt('email') }} as email,
        {{ txt('native_accent') }} as native_accent,
        {{ txt('country_of_origin') }} as country_of_origin,
        {{ txt('assigned_branch_id') }} as assigned_branch_id,
        {{ txt('agent_type') }} as agent_type,
        {{ txt('experience_level') }} as experience_level,
        {{ txt('languages') }} as languages,
        {{ txt('specialty') }} as specialty,
        {{ txt('hire_date') }} as hire_date,
        {{ txt('avg_csat') }} as avg_csat,
        {{ txt('total_monthly_interactions') }} as total_monthly_interactions,
        {{ txt('agent_status') }} as agent_status,
        {{ txt('work_shift') }} as work_shift,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.agent_id, r.employee_code, r.native_accent, r.country_of_origin,
        {{ country_code('r.country_of_origin') }} as country_code,
        r.assigned_branch_id, r.agent_type, r.experience_level, r.languages, r.specialty,
        {{ typed_columns(casts) }},
        r.agent_status, r.work_shift,
        (r.first_name is null or r.last_name is null or r.email is null) as _identity_missing,
        -- distinct agents holding the code, so a duplicated bronze row of the same agent
        -- (dq_duplicate_count) is not a collision
        coalesce(
            r.employee_code is not null
            and count(distinct r.agent_id) over (partition by r.employee_code) > 1,
            false
        ) as dq_employee_code_duplicate,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        t.*,
        (
            {{ any_null(['t.employee_code', 't.native_accent', 't.country_of_origin',
                         't.agent_type', 't.experience_level', 't.languages', 't.hire_date',
                         't.agent_status', 't.work_shift']) }}
            or t._identity_missing
        ) as dq_missing_required,
        {{ invalid_enum([('t.native_accent', 'accent'), ('t.country_of_origin', 'country'),
                         ('t.agent_type', 'agent_type'),
                         ('t.experience_level', 'experience_level'),
                         ('t.agent_status', 'agent_status'),
                         ('t.work_shift', 'work_shift')]) }} as dq_invalid_enum,
        (t.assigned_branch_id is not null and b.branch_id is null) as dq_orphan_branch,
        coalesce(t.avg_csat not between 1 and 5 or t.total_monthly_interactions < 0, false)
            as dq_out_of_range
    from typed as t
    left join (select branch_id from {{ ref('silver_branches') }}) as b
        on b.branch_id = t.assigned_branch_id
),

final as (
    {{ dedup_final('flagged', 'agent_id',
        ['agent_id', 'employee_code', 'native_accent', 'country_of_origin', 'country_code',
         'assigned_branch_id', 'agent_type', 'experience_level', 'languages', 'specialty',
         'hire_date', 'avg_csat', 'total_monthly_interactions', 'agent_status',
         'work_shift'] + flags,
        dq_any_flags, 'hire_date desc nulls last') }}
)

select * from final
