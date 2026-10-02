-- T16 dispute volume under three definitions over the whole snapshot, plus the snapshot span
-- and the customer base (for per-million-customer normalisation). The source has no dispute
-- label: "Cargo no reconocido" is the only named subcategory of category Transactions
-- (t16_complaint_subcategories.sql). strict = Complaint/Claim case types only; central = every
-- "Cargo no reconocido"; broad = every Transactions complaint (incl. NULL subcategory).
-- Aggregates only.
with complaints as (
    select * from {{ ref('silver_complaints') }} where process_date is not null
),

span as (
    select
        min(process_date) as first_date,
        max(process_date) as last_date,
        date_diff('day', min(process_date), max(process_date)) + 1 as days
    from complaints
)

select
    s.first_date,
    s.last_date,
    s.days,
    (select count(*) from {{ ref('silver_customers') }}) as customers,
    count(*) as complaints,
    count(*) filter (
        where k.subcategory = 'Cargo no reconocido' and k.case_type in ('Complaint', 'Claim')
    ) as disputes_strict,
    count(*) filter (where k.subcategory = 'Cargo no reconocido') as disputes_central,
    count(*) filter (where k.category = 'Transactions') as disputes_broad
from complaints as k
cross join span as s
group by s.first_date, s.last_date, s.days
