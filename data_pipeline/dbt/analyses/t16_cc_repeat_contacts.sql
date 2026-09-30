-- T16: repeat contacts. For every contact, is the same customer's next contact within 7 days, and
-- with the same reason? Split by was_resolved: if first-contact resolution meant anything,
-- unresolved contacts would repeat more. Aggregates only.
with ordered as (
    select
        reason_category,
        was_resolved,
        interaction_ts,
        lead(interaction_ts) over (partition by customer_id order by interaction_ts) as next_ts,
        lead(reason_category) over (partition by customer_id order by interaction_ts)
            as next_reason
    from {{ ref('silver_call_center_interactions') }}
)

select
    reason_category,
    was_resolved,
    count(*) as contacts,
    avg(cast(coalesce(next_ts <= interaction_ts + interval 7 day, false) as integer))
        as any_repeat_7d,
    avg(cast(coalesce(
        next_ts <= interaction_ts + interval 7 day and next_reason = reason_category, false
    ) as integer)) as same_reason_repeat_7d
from ordered
group by 1, 2
order by 1, 2
