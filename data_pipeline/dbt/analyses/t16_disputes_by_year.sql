-- T16: complaints and central disputes per snapshot year (365-day buckets from the first
-- process_date; the last bucket also takes the 1-2 leftover days). Checks that volume is stable
-- enough to annualise. Aggregates only.
with complaints as (
    select * from {{ ref('silver_complaints') }} where process_date is not null
),

first_day as (
    select min(process_date) as first_date from complaints
)

select
    least(cast(floor(date_diff('day', f.first_date, k.process_date) / 365) as integer), 2) + 1
        as snapshot_year,
    min(k.process_date) as from_date,
    max(k.process_date) as to_date,
    count(*) as complaints,
    count(*) filter (where k.subcategory = 'Cargo no reconocido') as disputes_central
from complaints as k
cross join first_day as f
group by 1
order by 1
