-- Offline only (ADR 0003): fraud_score distribution by the is_fraud label. Aggregates only.
select
    is_fraud,
    count(*) as rows,
    count(fraud_score) as scored_rows,
    round(avg(fraud_score), 2) as mean_score,
    round(quantile_cont(fraud_score, 0.10), 2) as p10,
    round(quantile_cont(fraud_score, 0.50), 2) as p50,
    round(quantile_cont(fraud_score, 0.90), 2) as p90,
    round(quantile_cont(fraud_score, 0.99), 2) as p99
from {{ ref('silver_transactions') }}
group by is_fraud
order by is_fraud
