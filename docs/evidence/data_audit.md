# Data audit: source behaviour that matters for disputes

Measured on the silver warehouse built by `uv run poe dbt-build` from the verified bronze
(manifest sha256 `8044bf9b34c6e38d872fa17928a7cedd2e5a513d64fd3b6d712117f21284d50d`, 23,495,188
rows). Aggregates only. The queries live in `data_pipeline/dbt/analyses/`; run them against the
warehouse with `{{ ref('x') }}` resolved to `x`, as `src/bankagent/silver/report.py` does. Each
row names its query. The `dq_*` counts are in `dq_report.md` and the fraud_score analysis is in
`fraud_score_thresholds.md`.

## Time and partitions

| # | Finding | Measurement | Query |
|---|---|---|---|
| A1 | `process_date` is a fixed UTC batch cutoff, not the local business date | `ts - process_date` falls in [6 h, 30 h] for transactions, campaign sends and digital events, and in [8 h, 32 h] for call center and complaints. The window is identical in AR, CO and MX (UTC-3/-5/-6), so the cutoff is 06:00 UTC (08:00 for call center and complaints), not each country's local midnight. Off-rule rows: 10-25 per country for transactions and 0-3 for call center and complaints, all exactly on the cutoff instant; digital events 0.18% (max 30.16 h). Surveys follow a different rule (lag up to 2 days). | `process_date_cutoff.sql` |
| A2 | The ~25% of rows whose UTC date is one day after `process_date` are not late arrivals | These are A1's cutoff (1,106,307 transactions). 0 rows in any table have a `process_date` later than the UTC event date (`dq_late_arrival` = 0). | `process_date_lag.sql` |

## Products, cards and lifecycle

| # | Finding | Measurement | Query |
|---|---|---|---|
| B1 | Many transactions fall outside the life of their product or customer | 827,989 happen before the product opened (18.71%) and 829,916 before the customer registered (18.76%). 462,295 happen after card expiry (10.45%; 31.42% of the 1,471,444 transactions on products with an expiry date). 525,056 are on Inactive or Closed customers (11.87%). Silver raises no dq flag for any of these. | `product_lifecycle.sql` |
| B2 | Only Active products have transactions | 339,963 of 339,965 Active products have transactions. Blocked (19,935), Closed (32,039) and Suspended (8,061) products have 0. | `product_status_activity.sql` |
| B3 | Many products are expired but still Active | 56,664 Active products (14.17% of 400,000) have `expiration_date` before the last process date, which is 50.1% of Active products that have an expiry date. | `product_status_activity.sql` |
| B4 | `last_updated_ts` values in the future | Values after the last transaction timestamp: customers 9,301 (6.20%, max 2027-06-15) and products 25,079 (6.27%). | `product_status_activity.sql`, ad hoc for customers |
| B5 | `products.last_transaction_ts` does not describe the transactions table | Only 427 of 305,723 products (0.14%) have a `last_transaction_ts` whose date matches their latest transaction. | `product_status_activity.sql` |
| B6 | Merchants are a small closed set | 24 merchants over 1,029,234 purchases, each with exactly 1 category | `purchase_twins.sql` |
| B7 | No repeated or double charges exist | Same product, merchant and amount: 0 repeats within 10 min, 0 within 1 day, 2 at any distance | `purchase_twins.sql` |
| B8 | Response codes carry no decline reason | Approved rows have `00` (3,867,312) or NULL. The codes `05`/`14`/`51`/`54` are spread evenly: about 52.5k each in Declined, 21k in Pending and 10.6k in Reversed. | `response_codes.sql` |

## Fraud signals (offline, ADR 0003)

| # | Finding | Measurement | Query |
|---|---|---|---|
| C1 | Non-fraud scores stop at exactly 30 | `>= 30`: 2,982 escalated, 2,373 fraud (precision 0.796). `>= 30.01`: 2,373 / 2,373 (precision 1.0). The [30, 40) band holds 969 rows, 360 of them fraud. Recall is 0.5498 of 4,316 fraud rows. | `fraud_score_bands.sql` |
| C2 | Alternative threshold `>= 35` | 2,198 / 2,198 (precision 1.0), recall 0.509. Against `>= 30` it trades 609 false escalations for 175 missed fraud rows. ADR 0003 keeps `>= 30`; Task 9 owns the final rule. | ad hoc, same filters as `fraud_score_thresholds.sql` |
| C3 | High scores are not declined | `fraud_score > 30`: 92.54% Approved vs 91.99% overall. 40 of 4,233 customers with a fraud transaction (0.94%) filed a complaint within 30 days; no placebo window was run, so this is descriptive only. | ad hoc |

## Complaints, identity and text

| # | Finding | Measurement | Query |
|---|---|---|---|
| D1 | `complaints.affected_product_id` never belongs to the complaining customer | 44,570 of 44,570 non-null values point to another customer's product (0 own, 0 missing). `dq_orphan_product` is 0 because the product exists; silver has no ownership flag on complaints. | `complaint_links.sql` |
| D2 | Complaints cannot be linked to calls | `origin_interaction_id` is non-null in 0 of 67,095 | `complaint_links.sql` |
| D3 | Claimed amount and currency are inconsistent | 1,040 amounts without a currency, 1,065 currencies without an amount | `complaint_links.sql` |
| D4 | Document type by country | MX: DNI 74,907, CURP 0. AR: DNI 29,842. CO: CC 15,039, CE 15,150, Pasaporte 15,062. | ad hoc on `silver_customers` |
| D5 | Transcripts are templates | 171,321 of 171,321 contain an unfilled `{...}` placeholder; 42 distinct `customer_text`; 1 distinct `detected_intents`; 0 rows not in `es` | ad hoc on `silver_call_transcripts` |

## What this changes for us

| Area | Consequence | Owner |
|---|---|---|
| Silver (T5 follow-up) | Add flags for B1, B4 and D1 (`dq_before_product_opening`, `dq_after_card_expiry`, `dq_future_last_updated`, complaint `dq_product_customer_mismatch`) so `dq_report.md` counts them. | Santiago |
| Gold / serving (T6) | Never serve `products.last_transaction_ts`; derive it from transactions (B5). Take `as_of_date` from transactions, not from `max(last_updated)` (B4). Card validity cannot come from `product_status` alone (B3). | Juan José |
| Policy (T9) | Transactions before opening or after expiry are common source artefacts and must not auto-reject a dispute (B1). The `>= 30` vs `>= 35` trade-off is documented (C2). Dispute windows computed on dates must name their calendar (A1). | Juan José |
| Tools / card block (T8, T19) | In curated mode no Blocked card has history, so blocked-card scenarios come from our ops store or fixtures (B2). Response codes cannot explain a decline (B8). | Juan José |
| Evaluation (T12, T17) | "Double charge" disputes have no organic example (B7), and complaints cannot ground cases (D1, D2). Eval cases come from fixtures or team-generated data. | Santiago |
| Auth (T7) | Mexican personas use DNI, not CURP (D4). | Juan José |
