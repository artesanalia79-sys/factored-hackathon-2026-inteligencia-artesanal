-- T16: is there any detectable link between complaints and call-center contacts?
-- origin_interaction_id is always NULL (data_audit.md D2), so this links by customer and time:
-- the share of complaints whose customer had a contact in a window before the complaint, and in
-- the 7 days after it, against a placebo window of the same length 90 days earlier. Only
-- complaints at least 97 days after the first contact are kept, so every placebo window is
-- fully covered by contact data. Equal shares mean no detectable link (placebo test), so
-- contacts per dispute cannot be measured and stays out of the model. Aggregates only.
with contacts as (
    select customer_id, interaction_ts from {{ ref('silver_call_center_interactions') }}
),

first_contact as (
    select min(interaction_ts) as first_ts from contacts
),

complaints as (
    select
        k.customer_id,
        k.creation_ts,
        k.subcategory is not distinct from 'Cargo no reconocido' as is_dispute,
        k.reception_channel = 'Call Center' as via_call_center
    from {{ ref('silver_complaints') }} as k
    cross join first_contact as f
    where k.creation_ts >= f.first_ts + interval 97 day
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
            and i.interaction_ts between k.creation_ts and k.creation_ts + interval 7 day
    ) as integer)) as contact_after_7d,
    avg(cast(exists (
        select 1 from contacts as i
        where i.customer_id = k.customer_id
            and i.interaction_ts between k.creation_ts - interval 97 day
            and k.creation_ts - interval 90 day
    ) as integer)) as placebo_7d
from complaints as k
group by 1, 2
order by 1, 2
