-- Grain of daily_exchange_rates: rows per day, distinct ordered pairs, self pairs, duplicates.
-- Expected (measured): 1,097 days x 12 ordered pairs = 13,164 rows, 0 duplicates.
select
    count(*) as rows,
    count(distinct rate_date) as days,
    count(distinct source_currency || '>' || target_currency) as ordered_pairs,
    count(*) filter (where source_currency = target_currency) as self_pairs,
    sum(dq_duplicate_count - 1) as extra_duplicate_rows,
    min(rate_date) as first_day,
    max(rate_date) as last_day
from {{ ref('silver_daily_exchange_rates') }}
