-- Offline only (ADR 0003): rows per fraud_score band and label, around the policy threshold 30.
-- Shows the fraud rows no score rule can reach (NULL score) and the non-fraud rows sitting
-- exactly on the threshold. Aggregates only.
select
    is_fraud,
    case
        when fraud_score is null then 'NULL'
        when fraud_score < 30 then '[0, 30)'
        when fraud_score = 30 then '= 30.00'
        when fraud_score < 40 then '(30, 40)'
        else '[40, 100]'
    end as band,
    count(*) as rows,
    min(fraud_score) as min_score,
    max(fraud_score) as max_score
from {{ ref('silver_transactions') }}
where is_fraud is not null
group by all
order by is_fraud, min(fraud_score) nulls first
