-- T16: are complaints and call-center contacts related at all? origin_interaction_id is always
-- NULL (data_audit.md D2), so this links by customer and time: the share of complaints whose
-- customer had a contact in a window before the complaint, against a placebo window of the same
-- length 90 days earlier. Equal shares mean the two streams are independent, so contacts per
-- dispute cannot be measured and stays an assumption. Aggregates only.
with complaints as (
    select
        customer_id,
        creation_ts,
        subcategory is not distinct from 'Cargo no reconocido' as is_dispute,
        reception_channel = 'Call Center' as via_call_center
    from {{ ref('silver_complaints') }}
    where creation_ts is not null
),

contacts as (
    select customer_id, interaction_ts from {{ ref('silver_call_center_interactions') }}
)

select
    k.is_dispute,
    k.via_call_center,
    count(*) as complaints,
    avg(cast(exists (
        select 1 from contacts as i
        where i.customer_id = k.customer_id
            and i.interaction_ts between k.creation_ts - interval 1 day and k.creation_ts
    ) as integer)) as contact_prior_1d,
    avg(cast(exists (
        select 1 from contacts as i
        where i.customer_id = k.customer_id
            and i.interaction_ts between k.creation_ts - interval 91 day
            and k.creation_ts - interval 90 day
    ) as integer)) as placebo_1d,
    avg(cast(exists (
        select 1 from contacts as i
        where i.customer_id = k.customer_id
            and i.interaction_ts between k.creation_ts - interval 7 day and k.creation_ts
    ) as integer)) as contact_prior_7d,
    avg(cast(exists (
        select 1 from contacts as i
        where i.customer_id = k.customer_id
            and i.interaction_ts between k.creation_ts - interval 97 day
            and k.creation_ts - interval 90 day
    ) as integer)) as placebo_7d
from complaints as k
group by 1, 2
order by 1, 2
