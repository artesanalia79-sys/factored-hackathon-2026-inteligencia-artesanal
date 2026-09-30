-- Offline only (ADR 0003): does escalating on fraud_score >= T beat random escalation?
-- For each threshold T and scope (all transactions / purchases), compare precision against the
-- base fraud rate at the same escalation rate. lift = precision / base_rate (1.0 = random).
-- Denominators include rows with a NULL fraud_score (they can never be escalated by a score
-- rule): `recall` = escalated fraud / ALL fraud rows; `recall_scored_only` excludes fraud rows
-- with a NULL score. Rows with a NULL is_fraud label are excluded. Aggregates only.
with labelled as (
    select
        fraud_score,
        is_fraud,
        transaction_type = 'Purchase' as is_purchase
    from {{ ref('silver_transactions') }}
    where is_fraud is not null
),
scopes as (
    select 'all' as scope, fraud_score, is_fraud from labelled
    union all
    select 'purchase' as scope, fraud_score, is_fraud from labelled where is_purchase
),
thresholds as (
    select cast(t as decimal(5,2)) as threshold from range(10, 100, 10) as r(t)
)
select
    s.scope,
    th.threshold,
    count(*) as labelled_rows,
    count(s.fraud_score) as scored_rows,
    count(*) filter (where s.is_fraud) as fraud_rows,
    count(*) filter (where s.is_fraud and s.fraud_score is null) as fraud_unscored,
    count(*) filter (where s.fraud_score >= th.threshold) as escalated,
    count(*) filter (where s.fraud_score >= th.threshold and s.is_fraud) as escalated_fraud,
    round(count(*) filter (where s.is_fraud) / count(*), 6) as base_rate,
    round(count(*) filter (where s.fraud_score >= th.threshold) / count(*), 6) as escalation_rate,
    round(
        count(*) filter (where s.fraud_score >= th.threshold and s.is_fraud)
        / nullif(count(*) filter (where s.fraud_score >= th.threshold), 0),
        6
    ) as precision,
    round(
        count(*) filter (where s.fraud_score >= th.threshold and s.is_fraud)
        / nullif(count(*) filter (where s.is_fraud), 0),
        6
    ) as recall,
    round(
        count(*) filter (where s.fraud_score >= th.threshold and s.is_fraud)
        / nullif(count(*) filter (where s.is_fraud and s.fraud_score is not null), 0),
        6
    ) as recall_scored_only,
    round(
        (
            count(*) filter (where s.fraud_score >= th.threshold and s.is_fraud)
            / nullif(count(*) filter (where s.fraud_score >= th.threshold), 0)
        ) / nullif(count(*) filter (where s.is_fraud) / count(*), 0),
        3
    ) as lift
from scopes as s
cross join thresholds as th
group by s.scope, th.threshold
order by s.scope, th.threshold
