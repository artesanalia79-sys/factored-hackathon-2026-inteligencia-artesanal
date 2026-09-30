-- response_code by transaction_status: which codes each status uses. Aggregates only.
select transaction_status, response_code, count(*) as rows
from {{ ref('silver_transactions') }}
group by all
order by 1, 2
