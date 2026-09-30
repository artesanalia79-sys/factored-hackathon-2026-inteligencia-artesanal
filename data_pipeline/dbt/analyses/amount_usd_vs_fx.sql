-- How the source amount_usd relates to amount x daily FX rate, per currency. Aggregates only.
-- The source uses a fixed rate (vars.amount_usd_fixed_rate); the dq flags check that rate. The
-- daily-FX comparison here is only a sensitivity number: how far a daily-rate conversion
-- (fx_rate_to_usd, joined on process_date) would move amount_usd.
-- implied_rate is amount_usd / amount; rel_diff compares amount_usd with amount x daily rate.
select
    currency,
    count(*) as rows,
    count(amount_usd) as with_amount_usd,
    count(*) filter (where dq_amount_usd_missing) as amount_usd_missing,
    count(*) filter (where dq_amount_usd_mismatch) as fixed_rate_mismatch,
    count(*) filter (where dq_missing_fx_rate) as missing_fx_rate,
    round(avg(amount_usd / nullif(amount, 0)), 8) as mean_implied_rate,
    round(stddev_samp(amount_usd / nullif(amount, 0)), 8) as sd_implied_rate,
    round(avg(fx_rate_to_usd), 8) as mean_daily_rate,
    round(quantile_cont(abs(amount * fx_rate_to_usd - amount_usd) / nullif(amount_usd, 0), 0.5), 4)
        as p50_rel_diff_daily,
    round(quantile_cont(abs(amount * fx_rate_to_usd - amount_usd) / nullif(amount_usd, 0), 0.99), 4)
        as p99_rel_diff_daily
from {{ ref('silver_transactions') }}
group by currency
order by currency
