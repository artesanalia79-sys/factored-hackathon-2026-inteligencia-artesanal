-- T16: every complaint category carries exactly one named subcategory (plus NULLs), so the
-- subcategory adds a label, not a finer split. Aggregates only.
select
    category,
    coalesce(subcategory, '(null)') as subcategory,
    count(*) as complaints
from {{ ref('silver_complaints') }}
group by 1, 2
order by 1, 3 desc
