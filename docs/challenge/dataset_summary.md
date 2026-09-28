# LATAM Bank Dataset: Summary

> Source: organizer "LATAM Bank Dataset Summary" (v1.0.0, generated July 2026), reformatted.
> Claims below are the organizer's documentation. Our own measurements (which differ in places)
> are tracked in `docs/evidence/dq_report.md` and `docs/decision_ledger.md`.

| Attribute | Value |
|---|---|
| Total records | ~19,000,000 (documented) |
| Tables | 13 |
| Countries | Mexico, Colombia, Argentina |
| Date range | 2023-06-17 to 2026-06-17 |
| Currencies | MXN, COP, ARS, USD |
| Languages | Spanish (Mexican, Colombian, Argentine accents) |
| Nature | **Fully synthetic**, generated for the Factored Datathon 2026. No real customer information. |

## Documented data quality challenges

| Challenge | Rate | Description |
|---|---|---|
| Duplicate records | ~2% | Duplicate entries across tables |
| Null values | ~5% | Missing data in non-mandatory fields |
| Late arrivals | Yes | Partitioned data may arrive late |
| Schema evolution | Yes | Table schemas may evolve over time |

## Tables

### Dimension tables

| Table | Rows | Description |
|---|---|---|
| customers | 150,000 | Bank customer dimension |
| products | 400,000 | Financial products of customers |
| branches | 350 | Physical bank branches |
| service_agents | 1,200 | Customer service agents |
| marketing_campaigns | 200 | Marketing campaigns |

### Fact tables

| Table | Rows | Description |
|---|---|---|
| transactions | 5,000,000 | Daily financial transactions |
| call_center_interactions | 800,000 | Call center interactions |
| call_transcripts | 200,000 | Call transcripts |
| satisfaction_surveys | 250,000 | CSAT / NPS / CES surveys |
| digital_events | 10,000,000 | App and web events |
| complaints | 80,000 | Complaints and claims (PQR) |
| campaign_sends | 2,000,000 | Individual campaign sends |

### Reference tables

| Table | Rows | Description |
|---|---|---|
| daily_exchange_rates | 3,000 | Daily FX rates |

## Important notes (organizer)

- All text data is Spanish with regional variation; **there is no Portuguese data**.
- Accent detection fields (Mexican, Colombian, Argentine).
- Transactions carry local currency and a USD conversion.
- Large fact tables are partitioned by date (year/month/day).
- Referential integrity is maintained, with a small percentage of orphans for testing.
- Full schema: see [data_dictionary.md](data_dictionary.md).
