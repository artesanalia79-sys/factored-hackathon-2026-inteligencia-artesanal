{%- set casts = [
    ('creation_ts', 'timestamp'), ('process_date', 'date'), ('claimed_amount', 'decimal(15,2)'),
    ('assignment_ts', 'timestamp'), ('first_response_ts', 'timestamp'),
    ('resolution_ts', 'timestamp'), ('closing_ts', 'timestamp'), ('sla_breached', 'boolean'),
    ('resolution_days', 'integer'), ('compensation_granted', 'decimal(15,2)'),
    ('resolution_satisfaction', 'integer'), ('is_repeat_complainer', 'boolean'),
] -%}
{%- set flags = ['dq_cast_failed', 'dq_missing_required', 'dq_invalid_enum', 'dq_orphan_customer',
                 'dq_orphan_product', 'dq_orphan_branch', 'dq_orphan_interaction',
                 'dq_orphan_agent', 'dq_out_of_range', 'dq_late_arrival'] -%}

with source as (
    select * from {{ source('bronze', 'complaints') }}
),

renamed as (
    select
        {{ txt('complaint_id') }} as complaint_id,
        {{ txt('creation_date') }} as creation_ts,
        {{ txt('process_date') }} as process_date,
        {{ txt('customer_id') }} as customer_id,
        {{ txt('case_type') }} as case_type,
        {{ txt('category') }} as category,
        {{ txt('subcategory') }} as subcategory,
        {{ txt('reception_channel') }} as reception_channel,
        {{ txt('affected_product_id') }} as affected_product_id,
        {{ txt('related_branch_id') }} as related_branch_id,
        {{ txt('origin_interaction_id') }} as origin_interaction_id,
        {{ txt('description') }} as description,
        {{ txt('claimed_amount') }} as claimed_amount,
        upper({{ txt('currency') }}) as currency,
        {{ txt('priority') }} as priority,
        {{ txt('status') }} as status,
        {{ txt('assigned_agent_id') }} as assigned_agent_id,
        {{ txt('assignment_date') }} as assignment_ts,
        {{ txt('first_response_date') }} as first_response_ts,
        {{ txt('resolution_date') }} as resolution_ts,
        {{ txt('closing_date') }} as closing_ts,
        {{ txt('sla_breached') }} as sla_breached,
        {{ txt('resolution_days') }} as resolution_days,
        {{ txt('resolution') }} as resolution,
        {{ txt('compensation_granted') }} as compensation_granted,
        {{ txt('resolution_satisfaction') }} as resolution_satisfaction,
        {{ txt('is_repeat_complainer') }} as is_repeat_complainer,
        {{ row_hash() }} as _row_hash
    from source
),

typed as (
    select
        r.complaint_id,
        {{ typed_columns(casts) }},
        r.customer_id, r.case_type, r.category, r.subcategory, r.reception_channel,
        r.affected_product_id, r.related_branch_id, r.origin_interaction_id, r.description,
        r.currency, r.priority, r.status, r.assigned_agent_id, r.resolution,
        {{ cast_failed(casts) }} as dq_cast_failed,
        r._row_hash
    from renamed as r
),

flagged as (
    select
        t.*,
        {{ any_null(['t.creation_ts', 't.process_date', 't.customer_id', 't.case_type',
                     't.category', 't.reception_channel', 't.description', 't.priority',
                     't.status', 't.sla_breached', 't.is_repeat_complainer']) }}
            as dq_missing_required,
        {{ invalid_enum([('t.case_type', 'case_type'),
                         ('t.reception_channel', 'reception_channel'),
                         ('t.currency', 'currency'), ('t.priority', 'priority'),
                         ('t.status', 'complaint_status')]) }} as dq_invalid_enum,
        (t.customer_id is not null and c.customer_id is null) as dq_orphan_customer,
        (t.affected_product_id is not null and p.product_id is null) as dq_orphan_product,
        (t.related_branch_id is not null and b.branch_id is null) as dq_orphan_branch,
        (t.origin_interaction_id is not null and i.interaction_id is null)
            as dq_orphan_interaction,
        (t.assigned_agent_id is not null and a.agent_id is null) as dq_orphan_agent,
        coalesce(
            t.claimed_amount < 0 or t.compensation_granted < 0 or t.resolution_days < 0
            or t.resolution_satisfaction not between 1 and 5
            or t.resolution_ts < t.creation_ts or t.closing_ts < t.creation_ts,
            false
        ) as dq_out_of_range,
        coalesce(t.process_date > cast(t.creation_ts as date), false) as dq_late_arrival
    from typed as t
    left join (select customer_id from {{ ref('silver_customers') }}) as c
        on c.customer_id = t.customer_id
    left join (select product_id from {{ ref('silver_products') }}) as p
        on p.product_id = t.affected_product_id
    left join (select branch_id from {{ ref('silver_branches') }}) as b
        on b.branch_id = t.related_branch_id
    left join (select interaction_id from {{ ref('silver_call_center_interactions') }}) as i
        on i.interaction_id = t.origin_interaction_id
    left join (select agent_id from {{ ref('silver_service_agents') }}) as a
        on a.agent_id = t.assigned_agent_id
),

final as (
    {{ dedup_final('flagged', 'complaint_id',
        ['complaint_id', 'creation_ts', 'process_date', 'customer_id', 'case_type', 'category',
         'subcategory', 'reception_channel', 'affected_product_id', 'related_branch_id',
         'origin_interaction_id', 'description', 'claimed_amount', 'currency', 'priority',
         'status', 'assigned_agent_id', 'assignment_ts', 'first_response_ts', 'resolution_ts',
         'closing_ts', 'sla_breached', 'resolution_days', 'resolution', 'compensation_granted',
         'resolution_satisfaction', 'is_repeat_complainer'] + flags,
        flags, 'process_date desc nulls last') }}
)

select * from final
