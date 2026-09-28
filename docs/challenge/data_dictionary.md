# LATAM Bank Complete Data Dictionary

> Source: organizer "LATAM Bank Complete Data Dictionary" (v1.0.0, generated September 2026),
> reformatted. **The organizer's data-access section (bucket, region, access keys) is intentionally
> removed.** Access is configured locally only: `aws configure --profile factored` plus
> `S3_BUCKET` / `AWS_DEFAULT_REGION` in the gitignored `.env` (see `.env.example`).
>
> Constraints below are as documented by the organizer. Measured deviations are tracked in
> `docs/evidence/dq_report.md`.

- Countries: Mexico, Colombia, Argentina. Date range: 2023-06-17 to 2026-06-17.
- Currencies: MXN, COP, ARS, USD. All text data in Spanish with regional variation.
- Documented DQ: ~2% duplicates, ~5% nulls in nullable fields, late arrivals, schema evolution.
- Fully synthetic; no real customer information.

## Dimension tables

### customers
Rows: 150,000 · Source: Core Banking · Partition: monthly_snapshot

| Column | Type | Description | Constraints |
|---|---|---|---|
| customer_id | VARCHAR(20) | Unique customer ID | PK, NOT NULL |
| document_number | VARCHAR(20) | Identity document number | NOT NULL, UNIQUE |
| document_type | VARCHAR(10) | Document type (DNI, CURP, CC, CE, Passport) | NOT NULL |
| first_name | VARCHAR(100) | First name (Spanish) | NOT NULL |
| last_name | VARCHAR(100) | Last name (Spanish) | NOT NULL |
| date_of_birth | DATE | Date of birth | NOT NULL |
| gender | VARCHAR(1) | Gender (M, F, O) | |
| email | VARCHAR(100) | Email address | |
| mobile_phone | VARCHAR(20) | Mobile phone | |
| landline_phone | VARCHAR(20) | Landline phone | |
| address | VARCHAR(200) | Full address (Spanish) | |
| city | VARCHAR(100) | City of residence | NOT NULL |
| state | VARCHAR(100) | State/Province | NOT NULL |
| country | VARCHAR(50) | Country (Mexico, Colombia, Argentina) | NOT NULL |
| postal_code | VARCHAR(10) | Postal code | |
| detected_accent | VARCHAR(50) | mexican, colombian, argentine, neutral | |
| segment | VARCHAR(50) | Premium, Plus, Basic, Student | NOT NULL |
| credit_score | INTEGER | Credit score (300–850) | |
| estimated_monthly_income | DECIMAL(12,2) | Estimated monthly income (local currency) | |
| occupation | VARCHAR(100) | Occupation | |
| marital_status | VARCHAR(20) | Marital status | |
| education_level | VARCHAR(50) | Education level | |
| registration_date | TIMESTAMP | Registration date | NOT NULL |
| registration_branch_id | VARCHAR(20) | Branch where registered | FK, NOT NULL |
| customer_status | VARCHAR(20) | Active, Inactive, Suspended, Closed | NOT NULL |
| last_updated | TIMESTAMP | Last record update | NOT NULL |
| accepts_marketing | BOOLEAN | Accepts marketing | NOT NULL |

### products
Rows: 400,000 · Source: Core Banking · Partition: monthly_snapshot

| Column | Type | Description | Constraints |
|---|---|---|---|
| product_id | VARCHAR(20) | Unique product ID | PK, NOT NULL |
| customer_id | VARCHAR(20) | Owner customer ID | FK, NOT NULL |
| product_type | VARCHAR(50) | Checking Account, Savings Account, Credit Card, Debit Card, Personal Loan, Mortgage, Investment, ... | NOT NULL |
| product_number | VARCHAR(30) | Account/card/policy number | NOT NULL, UNIQUE |
| currency | VARCHAR(3) | MXN, COP, ARS, USD | NOT NULL |
| current_balance | DECIMAL(15,2) | Current balance | NOT NULL |
| credit_limit | DECIMAL(15,2) | Credit limit (credit products) | |
| interest_rate | DECIMAL(5,2) | Annual interest rate (%) | |
| opening_date | DATE | Opening date | NOT NULL |
| expiration_date | DATE | Expiration date (term products) | |
| opening_branch_id | VARCHAR(20) | Opening branch | FK, NOT NULL |
| product_status | VARCHAR(20) | Active, Blocked, Closed, Suspended | NOT NULL |
| opening_channel | VARCHAR(30) | Branch, Web, App, Call Center | NOT NULL |
| has_linked_app | BOOLEAN | Linked to mobile app | NOT NULL |
| days_past_due | INTEGER | Days past due (credits) | |
| last_transaction_date | TIMESTAMP | Last transaction date | |
| last_updated | TIMESTAMP | Last record update | NOT NULL |

### branches
Rows: 350 · Source: Internal · Partition: full_snapshot

| Column | Type | Description | Constraints |
|---|---|---|---|
| branch_id | VARCHAR(20) | Unique branch ID | PK, NOT NULL |
| branch_code | VARCHAR(10) | Internal branch code | NOT NULL, UNIQUE |
| branch_name | VARCHAR(100) | Branch name | NOT NULL |
| branch_type | VARCHAR(30) | Main, Express, Premium, Corporate | NOT NULL |
| address | VARCHAR(200) | Address (Spanish) | NOT NULL |
| city | VARCHAR(100) | City | NOT NULL |
| state | VARCHAR(100) | State/Province | NOT NULL |
| country | VARCHAR(50) | Country | NOT NULL |
| postal_code | VARCHAR(10) | Postal code | |
| geographic_zone | VARCHAR(50) | Urban, Suburban, Rural | NOT NULL |
| phone | VARCHAR(20) | Contact phone | NOT NULL |
| email | VARCHAR(100) | Branch email | |
| opening_time | TIME | Opening time | NOT NULL |
| closing_time | TIME | Closing time | NOT NULL |
| has_atms | BOOLEAN | Has ATMs | NOT NULL |
| atm_count | INTEGER | Number of ATMs | |
| has_teller_windows | BOOLEAN | Has teller windows | NOT NULL |
| teller_window_count | INTEGER | Number of teller windows | |
| latitude | DECIMAL(10,7) | Latitude | |
| longitude | DECIMAL(10,7) | Longitude | |
| branch_opening_date | DATE | Branch opening date | NOT NULL |
| branch_status | VARCHAR(20) | Active, Temporarily Closed, Closed | NOT NULL |

### service_agents
Rows: 1,200 · Source: Internal · Partition: monthly_snapshot

| Column | Type | Description | Constraints |
|---|---|---|---|
| agent_id | VARCHAR(20) | Unique agent ID | PK, NOT NULL |
| employee_code | VARCHAR(15) | Employee code | NOT NULL, UNIQUE |
| first_name | VARCHAR(100) | First name | NOT NULL |
| last_name | VARCHAR(100) | Last name | NOT NULL |
| email | VARCHAR(100) | Corporate email | NOT NULL |
| phone | VARCHAR(20) | Contact phone | |
| native_accent | VARCHAR(50) | mexican, colombian, argentine | NOT NULL |
| country_of_origin | VARCHAR(50) | Country of origin | NOT NULL |
| assigned_branch_id | VARCHAR(20) | Assigned branch | FK |
| agent_type | VARCHAR(30) | Phone, In-Person, Digital, Hybrid | NOT NULL |
| experience_level | VARCHAR(20) | Junior, Mid-Senior, Senior, Specialist | NOT NULL |
| languages | VARCHAR(100) | Languages spoken | NOT NULL |
| specialty | VARCHAR(100) | Specialty | |
| hire_date | DATE | Hire date | NOT NULL |
| avg_csat | DECIMAL(3,2) | Average CSAT (1–5) | |
| total_monthly_interactions | INTEGER | Interactions last month | |
| agent_status | VARCHAR(20) | Active, Vacation, Leave, Inactive | NOT NULL |
| work_shift | VARCHAR(20) | Morning, Afternoon, Night, Rotating | NOT NULL |

### marketing_campaigns
Rows: 200 · Source: Internal · Partition: full_snapshot

| Column | Type | Description | Constraints |
|---|---|---|---|
| campaign_id | VARCHAR(20) | Unique campaign ID | PK, NOT NULL |
| campaign_name | VARCHAR(150) | Campaign name | NOT NULL |
| description | TEXT | Description | |
| campaign_type | VARCHAR(50) | Email, SMS, Push, WhatsApp, Voice, Mix | NOT NULL |
| campaign_objective | VARCHAR(100) | Acquisition, Retention, Cross-sell, Up-sell, Reactivation | NOT NULL |
| promoted_product | VARCHAR(50) | Promoted product | |
| target_segment | VARCHAR(50) | Target segment | |
| target_country | VARCHAR(50) | Target country | |
| start_date | DATE | Start date | NOT NULL |
| end_date | DATE | End date | NOT NULL |
| budget | DECIMAL(12,2) | Budget | |
| campaign_status | VARCHAR(20) | Planned, Active, Paused, Completed | NOT NULL |
| expected_conversion_rate | DECIMAL(5,2) | Expected conversion rate (%) | |

## Fact tables

### transactions
Rows: 5,000,000 · Source: Core Banking · Partition: daily

| Column | Type | Description | Constraints |
|---|---|---|---|
| transaction_id | VARCHAR(30) | Unique transaction ID | PK, NOT NULL |
| transaction_date | TIMESTAMP | Transaction date and time | NOT NULL |
| process_date | DATE | Process date (partition key) | NOT NULL |
| product_id | VARCHAR(20) | Product ID | FK, NOT NULL |
| customer_id | VARCHAR(20) | Customer ID | FK, NOT NULL |
| transaction_type | VARCHAR(50) | Deposit, Withdrawal, Transfer, Payment, Purchase, Adjustment | NOT NULL |
| transaction_category | VARCHAR(50) | Food, Transport, Services, Entertainment, Health, Other | |
| amount | DECIMAL(15,2) | Amount | NOT NULL |
| currency | VARCHAR(3) | Currency | NOT NULL |
| amount_usd | DECIMAL(15,2) | Amount in USD | |
| channel | VARCHAR(30) | ATM, Branch, Web, App, POS, Transfer | NOT NULL |
| branch_id | VARCHAR(20) | Branch ID (if applicable) | FK |
| merchant_name | VARCHAR(150) | Merchant name (purchases) | |
| merchant_category | VARCHAR(50) | MCC merchant category | |
| transaction_country | VARCHAR(50) | Country where it occurred | NOT NULL |
| transaction_city | VARCHAR(100) | City where it occurred | |
| transaction_status | VARCHAR(20) | Approved, Declined, Pending, Reversed | NOT NULL |
| response_code | VARCHAR(10) | System response code | |
| is_fraud | BOOLEAN | Marked as fraud (**retrospective label; never used at runtime, see ADR-0003**) | NOT NULL |
| fraud_score | DECIMAL(5,2) | Fraud risk score (0–100) | |
| latitude | DECIMAL(10,7) | Latitude | |
| longitude | DECIMAL(10,7) | Longitude | |

### call_center_interactions
Rows: 800,000 · Source: Contact Center · Partition: daily

| Column | Type | Description | Constraints |
|---|---|---|---|
| interaction_id | VARCHAR(30) | Unique interaction ID | PK, NOT NULL |
| interaction_date | TIMESTAMP | Date and time | NOT NULL |
| process_date | DATE | Process date (partition key) | NOT NULL |
| customer_id | VARCHAR(20) | Customer ID | FK, NOT NULL |
| agent_id | VARCHAR(20) | Agent ID | FK |
| interaction_type | VARCHAR(30) | Inbound Call, Outbound Call, Chat, Email, Video | NOT NULL |
| channel | VARCHAR(30) | Phone, Web Chat, WhatsApp, Email, App | NOT NULL |
| contact_reason | VARCHAR(100) | Main contact reason | NOT NULL |
| reason_category | VARCHAR(50) | Transactional, Product, Technical, Commercial, Complaint | NOT NULL |
| duration_seconds | INTEGER | Duration | |
| wait_time_seconds | INTEGER | Wait time | |
| was_resolved | BOOLEAN | Resolved on first call (FCR) | |
| requires_followup | BOOLEAN | Requires follow-up | NOT NULL |
| detected_sentiment | VARCHAR(20) | Positive, Neutral, Negative, Very Negative | |
| sentiment_score | DECIMAL(3,2) | Sentiment score (-1 to 1) | |
| customer_detected_accent | VARCHAR(50) | Customer accent | |
| agent_used_accent | VARCHAR(50) | Agent accent | |
| was_escalated | BOOLEAN | Escalated to supervisor | NOT NULL |
| mentioned_products | VARCHAR(200) | Product IDs mentioned (comma-separated) | |
| has_transcript | BOOLEAN | Has transcript | NOT NULL |
| has_recording | BOOLEAN | Has recording | NOT NULL |

### call_transcripts
Rows: 200,000 · Source: Contact Center · Partition: daily

| Column | Type | Description | Constraints |
|---|---|---|---|
| transcript_id | VARCHAR(30) | Unique transcript ID | PK, NOT NULL |
| interaction_id | VARCHAR(30) | Related interaction | FK, NOT NULL |
| process_date | DATE | Process date (partition key) | NOT NULL |
| customer_id | VARCHAR(20) | Customer ID | FK, NOT NULL |
| agent_id | VARCHAR(20) | Agent ID | FK, NOT NULL |
| full_text | TEXT | Full transcript (Spanish) | NOT NULL |
| customer_text | TEXT | Customer turns only | |
| agent_text | TEXT | Agent turns only | |
| detected_language | VARCHAR(10) | Main language | NOT NULL |
| detected_accent | VARCHAR(50) | Accent | |
| accent_confidence | DECIMAL(3,2) | Accent confidence (0–1) | |
| detected_keywords | VARCHAR(500) | Keywords | |
| mentioned_entities | TEXT | Extracted entities (JSON) | |
| detected_intents | VARCHAR(300) | Intents | |
| main_topics | VARCHAR(300) | Main topics | |
| transcription_model | VARCHAR(50) | Whisper, Google STT, etc. | NOT NULL |
| audio_quality | VARCHAR(20) | High, Medium, Low | |
| duration_seconds | INTEGER | Call duration | NOT NULL |

### satisfaction_surveys
Rows: 250,000 · Source: Contact Center · Partition: daily

| Column | Type | Description | Constraints |
|---|---|---|---|
| survey_id | VARCHAR(30) | Unique survey ID | PK, NOT NULL |
| survey_date | TIMESTAMP | Response date and time | NOT NULL |
| process_date | DATE | Process date (partition key) | NOT NULL |
| interaction_id | VARCHAR(30) | Evaluated interaction | FK |
| customer_id | VARCHAR(20) | Customer ID | FK, NOT NULL |
| agent_id | VARCHAR(20) | Evaluated agent | FK |
| survey_type | VARCHAR(20) | CSAT, NPS, CES | NOT NULL |
| send_channel | VARCHAR(30) | Email, SMS, IVR, App, Web | NOT NULL |
| main_score | INTEGER | 1–5 for CSAT, 0–10 for NPS | NOT NULL |
| nps_category | VARCHAR(20) | Promoter, Passive, Detractor | |
| question_1_text | TEXT | Question 1 | |
| question_1_response | INTEGER | Response (1–5) | |
| question_2_text | TEXT | Question 2 | |
| question_2_response | INTEGER | Response (1–5) | |
| question_3_text | TEXT | Question 3 | |
| question_3_response | INTEGER | Response (1–5) | |
| open_comments | TEXT | Comments (Spanish) | |
| comment_sentiment | VARCHAR(20) | Comment sentiment | |
| response_time_hours | DECIMAL(8,2) | Hours between interaction and response | |
| campaign_response_rate | DECIMAL(5,2) | Campaign response rate (%) | |

### digital_events
Rows: 10,000,000 · Source: Digital Banking · Partition: daily

| Column | Type | Description | Constraints |
|---|---|---|---|
| event_id | VARCHAR(30) | Unique event ID | PK, NOT NULL |
| event_date | TIMESTAMP | Date and time | NOT NULL |
| process_date | DATE | Process date (partition key) | NOT NULL |
| customer_id | VARCHAR(20) | Customer ID | FK |
| session_id | VARCHAR(50) | Session ID | NOT NULL |
| event_type | VARCHAR(50) | PageView, Click, FormSubmit, Login, Logout, Error, Purchase | NOT NULL |
| event_category | VARCHAR(50) | Navigation, Transaction, Authentication, Product | NOT NULL |
| channel | VARCHAR(30) | Android App, iOS App, Desktop Web, Mobile Web | NOT NULL |
| platform | VARCHAR(30) | Android, iOS, Windows, MacOS, Linux | |
| browser | VARCHAR(50) | Browser | |
| app_version | VARCHAR(20) | App version | |
| page_url | VARCHAR(300) | Page URL | |
| page_title | VARCHAR(200) | Page title | |
| action | VARCHAR(100) | Action | |
| element_id | VARCHAR(100) | Element ID | |
| product_id | VARCHAR(20) | Related product | FK |
| event_value | DECIMAL(15,2) | Monetary value | |
| duration_seconds | INTEGER | Duration | |
| ip_address | VARCHAR(45) | IP address | |
| ip_country | VARCHAR(50) | Country by IP | |
| ip_city | VARCHAR(100) | City by IP | |
| is_mobile | BOOLEAN | From mobile device | NOT NULL |
| referrer | VARCHAR(300) | Referrer URL | |
| utm_source | VARCHAR(100) | UTM source | |
| utm_medium | VARCHAR(100) | UTM medium | |
| utm_campaign | VARCHAR(100) | UTM campaign | |

### complaints
Rows: 80,000 · Source: PQR · Partition: daily

| Column | Type | Description | Constraints |
|---|---|---|---|
| complaint_id | VARCHAR(30) | Unique complaint/claim ID | PK, NOT NULL |
| creation_date | TIMESTAMP | Creation date | NOT NULL |
| process_date | DATE | Process date (partition key) | NOT NULL |
| customer_id | VARCHAR(20) | Customer ID | FK, NOT NULL |
| case_type | VARCHAR(30) | Complaint, Claim, Request, Suggestion | NOT NULL |
| category | VARCHAR(100) | Case category | NOT NULL |
| subcategory | VARCHAR(100) | Subcategory | |
| reception_channel | VARCHAR(30) | Call Center, Email, Web, App, Branch, Regulator | NOT NULL |
| affected_product_id | VARCHAR(20) | Affected product | FK |
| related_branch_id | VARCHAR(20) | Related branch | FK |
| origin_interaction_id | VARCHAR(30) | Originating interaction | FK |
| description | TEXT | Description (Spanish) | NOT NULL |
| claimed_amount | DECIMAL(15,2) | Claimed amount | |
| currency | VARCHAR(3) | Currency | |
| priority | VARCHAR(20) | Low, Medium, High, Critical | NOT NULL |
| status | VARCHAR(30) | Open, In Process, Escalated, Resolved, Closed, Rejected | NOT NULL |
| assigned_agent_id | VARCHAR(20) | Assigned agent | FK |
| assignment_date | TIMESTAMP | Assignment date | |
| first_response_date | TIMESTAMP | First response date | |
| resolution_date | TIMESTAMP | Resolution date | |
| closing_date | TIMESTAMP | Closing date | |
| sla_breached | BOOLEAN | SLA breached | NOT NULL |
| resolution_days | INTEGER | Days to resolution | |
| resolution | TEXT | Resolution (Spanish) | |
| compensation_granted | DECIMAL(15,2) | Compensation granted | |
| resolution_satisfaction | INTEGER | Satisfaction (1–5) | |
| is_repeat_complainer | BOOLEAN | Previous complaints in last 90 days | NOT NULL |

### campaign_sends
Rows: 2,000,000 · Source: Internal · Partition: daily

| Column | Type | Description | Constraints |
|---|---|---|---|
| send_id | VARCHAR(30) | Unique send ID | PK, NOT NULL |
| send_date | TIMESTAMP | Send date and time | NOT NULL |
| process_date | DATE | Process date (partition key) | NOT NULL |
| campaign_id | VARCHAR(20) | Campaign | FK, NOT NULL |
| customer_id | VARCHAR(20) | Recipient | FK, NOT NULL |
| send_channel | VARCHAR(30) | Email, SMS, Push, WhatsApp, Voice | NOT NULL |
| template_used | VARCHAR(100) | Template | |
| subject | VARCHAR(200) | Subject | |
| send_status | VARCHAR(20) | Sent, Failed, Bounced, Blocked | NOT NULL |
| was_delivered | BOOLEAN | Delivered | NOT NULL |
| was_opened | BOOLEAN | Opened | |
| open_date | TIMESTAMP | Open date | |
| was_clicked | BOOLEAN | Clicked | |
| click_date | TIMESTAMP | First click date | |
| click_count | INTEGER | Click count | |
| had_conversion | BOOLEAN | Converted | NOT NULL |
| conversion_date | TIMESTAMP | Conversion date | |
| conversion_value | DECIMAL(15,2) | Conversion value | |
| open_device | VARCHAR(30) | Device | |
| open_country | VARCHAR(50) | Country where opened | |
| failure_reason | VARCHAR(200) | Failure reason | |
| send_cost | DECIMAL(10,4) | Send cost | |

## Reference tables

### daily_exchange_rates
Rows: 3,000 · Source: Reference · Partition: daily

| Column | Type | Description | Constraints |
|---|---|---|---|
| date | DATE | Rate date | PK, NOT NULL |
| source_currency | VARCHAR(3) | Source currency | PK, NOT NULL |
| target_currency | VARCHAR(3) | Target currency | PK, NOT NULL |
| exchange_rate | DECIMAL(12,6) | Exchange rate | NOT NULL |
| buy_rate | DECIMAL(12,6) | Bank buy rate | |
| sell_rate | DECIMAL(12,6) | Bank sell rate | |
| source | VARCHAR(50) | Rate source | |

## Foreign keys (documented)

- **customers**: products, transactions, call_center_interactions, call_transcripts,
  satisfaction_surveys, digital_events, complaints, campaign_sends → `customer_id`.
- **branches**: customers.registration_branch_id, products.opening_branch_id,
  service_agents.assigned_branch_id, transactions.branch_id, complaints.related_branch_id → `branch_id`.
- **service_agents**: call_center_interactions, call_transcripts, satisfaction_surveys
  (`agent_id`), complaints.assigned_agent_id → `agent_id`.
- **products**: transactions, digital_events (`product_id`), complaints.affected_product_id → `product_id`.
- **marketing_campaigns**: campaign_sends.campaign_id → `campaign_id`.
- **call_center_interactions**: call_transcripts, satisfaction_surveys (`interaction_id`),
  complaints.origin_interaction_id → `interaction_id`.
