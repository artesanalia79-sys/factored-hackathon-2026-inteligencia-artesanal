"""Deterministic synthetic bronze (all-string parquet + manifest) for CI silver tests.

No organizer data: every value is invented here. A few defects are seeded on purpose so tests
can assert that the matching dq_* flags fire (see `SEEDED_DEFECTS`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from bankagent.ingest.catalog import TABLES
from bankagent.ingest.manifest import FileEntry, Manifest, sha256_file, write_manifest

Row = dict[str, str | None]

COLUMNS: dict[str, list[str]] = {
    "customers": [
        "customer_id",
        "document_number",
        "document_type",
        "first_name",
        "last_name",
        "date_of_birth",
        "gender",
        "email",
        "mobile_phone",
        "landline_phone",
        "address",
        "city",
        "state",
        "country",
        "postal_code",
        "detected_accent",
        "segment",
        "credit_score",
        "estimated_monthly_income",
        "occupation",
        "marital_status",
        "education_level",
        "registration_date",
        "registration_branch_id",
        "customer_status",
        "last_updated",
        "accepts_marketing",
    ],
    "products": [
        "product_id",
        "customer_id",
        "product_type",
        "product_number",
        "currency",
        "current_balance",
        "credit_limit",
        "interest_rate",
        "opening_date",
        "expiration_date",
        "opening_branch_id",
        "product_status",
        "opening_channel",
        "has_linked_app",
        "days_past_due",
        "last_transaction_date",
        "last_updated",
    ],
    "branches": [
        "branch_id",
        "branch_code",
        "branch_name",
        "branch_type",
        "address",
        "city",
        "state",
        "country",
        "postal_code",
        "geographic_zone",
        "phone",
        "email",
        "opening_time",
        "closing_time",
        "has_atms",
        "atm_count",
        "has_teller_windows",
        "teller_window_count",
        "latitude",
        "longitude",
        "branch_opening_date",
        "branch_status",
    ],
    "service_agents": [
        "agent_id",
        "employee_code",
        "first_name",
        "last_name",
        "email",
        "phone",
        "native_accent",
        "country_of_origin",
        "assigned_branch_id",
        "agent_type",
        "experience_level",
        "languages",
        "specialty",
        "hire_date",
        "avg_csat",
        "total_monthly_interactions",
        "agent_status",
        "work_shift",
    ],
    "marketing_campaigns": [
        "campaign_id",
        "campaign_name",
        "description",
        "campaign_type",
        "campaign_objective",
        "promoted_product",
        "target_segment",
        "target_country",
        "start_date",
        "end_date",
        "budget",
        "campaign_status",
        "expected_conversion_rate",
    ],
    "transactions": [
        "transaction_id",
        "transaction_date",
        "process_date",
        "product_id",
        "customer_id",
        "transaction_type",
        "transaction_category",
        "amount",
        "currency",
        "amount_usd",
        "channel",
        "branch_id",
        "merchant_name",
        "merchant_category",
        "transaction_country",
        "transaction_city",
        "transaction_status",
        "response_code",
        "is_fraud",
        "fraud_score",
        "latitude",
        "longitude",
    ],
    "call_center_interactions": [
        "interaction_id",
        "interaction_date",
        "process_date",
        "customer_id",
        "agent_id",
        "interaction_type",
        "channel",
        "contact_reason",
        "reason_category",
        "duration_seconds",
        "wait_time_seconds",
        "was_resolved",
        "requires_followup",
        "detected_sentiment",
        "sentiment_score",
        "customer_detected_accent",
        "agent_used_accent",
        "was_escalated",
        "mentioned_products",
        "has_transcript",
        "has_recording",
    ],
    "call_transcripts": [
        "transcript_id",
        "interaction_id",
        "process_date",
        "customer_id",
        "agent_id",
        "full_text",
        "customer_text",
        "agent_text",
        "detected_language",
        "detected_accent",
        "accent_confidence",
        "detected_keywords",
        "mentioned_entities",
        "detected_intents",
        "main_topics",
        "transcription_model",
        "audio_quality",
        "duration_seconds",
    ],
    "satisfaction_surveys": [
        "survey_id",
        "survey_date",
        "process_date",
        "interaction_id",
        "customer_id",
        "agent_id",
        "survey_type",
        "send_channel",
        "main_score",
        "nps_category",
        "question_1_text",
        "question_1_response",
        "question_2_text",
        "question_2_response",
        "question_3_text",
        "question_3_response",
        "open_comments",
        "comment_sentiment",
        "response_time_hours",
        "campaign_response_rate",
    ],
    "digital_events": [
        "event_id",
        "event_date",
        "process_date",
        "customer_id",
        "session_id",
        "event_type",
        "event_category",
        "channel",
        "platform",
        "browser",
        "app_version",
        "page_url",
        "page_title",
        "action",
        "element_id",
        "product_id",
        "event_value",
        "duration_seconds",
        "ip_address",
        "ip_country",
        "ip_city",
        "is_mobile",
        "referrer",
        "utm_source",
        "utm_medium",
        "utm_campaign",
    ],
    "complaints": [
        "complaint_id",
        "creation_date",
        "process_date",
        "customer_id",
        "case_type",
        "category",
        "subcategory",
        "reception_channel",
        "affected_product_id",
        "related_branch_id",
        "origin_interaction_id",
        "description",
        "claimed_amount",
        "currency",
        "priority",
        "status",
        "assigned_agent_id",
        "assignment_date",
        "first_response_date",
        "resolution_date",
        "closing_date",
        "sla_breached",
        "resolution_days",
        "resolution",
        "compensation_granted",
        "resolution_satisfaction",
        "is_repeat_complainer",
    ],
    "campaign_sends": [
        "send_id",
        "send_date",
        "process_date",
        "campaign_id",
        "customer_id",
        "send_channel",
        "template_used",
        "subject",
        "send_status",
        "was_delivered",
        "was_opened",
        "open_date",
        "was_clicked",
        "click_date",
        "click_count",
        "had_conversion",
        "conversion_date",
        "conversion_value",
        "open_device",
        "open_country",
        "failure_reason",
        "send_cost",
    ],
    "daily_exchange_rates": [
        "date",
        "source_currency",
        "target_currency",
        "exchange_rate",
        "buy_rate",
        "sell_rate",
        "source",
    ],
}

# What `generate_synthetic_bronze` breaks on purpose (model, business key, expected flag).
SEEDED_DEFECTS: tuple[tuple[str, str, str], ...] = (
    ("silver_transactions", "TXN-FX-0003", "dq_cast_failed"),
    ("silver_transactions", "TXN-FX-0004", "dq_out_of_range"),
    ("silver_transactions", "TXN-FX-0005", "dq_late_arrival"),
    ("silver_transactions", "TXN-FX-0006", "dq_invalid_enum"),
    ("silver_transactions", "TXN-FX-0007", "dq_orphan_customer"),
    ("silver_transactions", "TXN-FX-0008", "dq_missing_fx_rate"),
    ("silver_transactions", "TXN-FX-0009", "dq_amount_usd_mismatch"),
    ("silver_transactions", "TXN-FX-0011", "dq_cast_failed"),  # lossy decimal cast
    ("silver_transactions", "TXN-FX-0012", "dq_cast_failed"),  # timestamp with `Z` offset
    ("silver_transactions", "TXN-FX-0013", "dq_amount_usd_missing"),
    ("silver_transactions", "TXN-FX-0016", "dq_product_customer_mismatch"),
    ("silver_transactions", "TXN-FX-0017", "dq_orphan_product"),
    ("silver_customers", "CUST-FX-003", "dq_missing_required"),
    ("silver_customers", "CUST-FX-003", "dq_orphan_branch"),
    ("silver_customers", "CUST-FX-002", "dq_out_of_range"),
    ("silver_products", "PRD-FX-003", "dq_product_number_duplicate"),
    ("silver_products", "PRD-FX-005", "dq_product_number_duplicate"),
    ("silver_service_agents", "AGT-FX-02", "dq_employee_code_duplicate"),
    ("silver_service_agents", "AGT-FX-03", "dq_employee_code_duplicate"),
    ("silver_service_agents", "AGT-FX-04", "dq_orphan_branch"),
)
# (model, flag) pairs that are columns but are not folded into dq_any: the source branch FK is a
# random token on almost every customer and agent row (see the model SQL).
DQ_ANY_EXCLUDED: frozenset[tuple[str, str]] = frozenset(
    {("silver_customers", "dq_orphan_branch"), ("silver_service_agents", "dq_orphan_branch")}
)
# Rows that look suspicious but must raise no dq flag at all (dq_any = false):
# - TXN 10: USD with NULL amount_usd (expected; T6 fills it with amount);
# - TXN 14: UTC date is the day after the last FX date, but its process_date has a rate;
# - TXN 15 / 18: NULL / unparseable is_fraud label (the label feeds no dq flag).
CLEAN_KEYS: tuple[tuple[str, str], ...] = (
    ("silver_transactions", "TXN-FX-0001"),
    ("silver_transactions", "TXN-FX-0010"),
    ("silver_transactions", "TXN-FX-0014"),
    ("silver_transactions", "TXN-FX-0015"),
    ("silver_transactions", "TXN-FX-0018"),
)
# Same business key twice in bronze: collapsed, counted, and NOT a code collision.
DUPLICATED_KEYS: tuple[tuple[str, str], ...] = (
    ("silver_transactions", "TXN-FX-0002"),
    ("silver_products", "PRD-FX-002"),
    ("silver_service_agents", "AGT-FX-01"),
)
# Bronze rows without a business key (excluded from silver, counted as dropped_keyless).
KEYLESS_ROWS: dict[str, int] = {"silver_customers": 1, "silver_transactions": 1}
# Error-severity tests the seeded defects must trip (invalid status, orphan customer/product).
EXPECTED_FAILING_TESTS: frozenset[str] = frozenset(
    {
        "accepted_values_silver_transactions_transaction_status",
        "relationships_silver_transactions_customer_id",
        "relationships_silver_transactions_product_id",
    }
)

BASE_DAY = date(2026, 6, 1)
CURRENCIES = ("MXN", "COP", "ARS", "USD")
USD_PER_UNIT = {"MXN": "0.058000", "COP": "0.000250", "ARS": "0.002857", "USD": "1.000000"}


def _day(offset: int = 0) -> str:
    return (BASE_DAY + timedelta(days=offset)).isoformat()


def _ts(offset: int = 0, hour: int = 12) -> str:
    return f"{_day(offset)} {hour:02d}:00:00"


def _rows() -> dict[str, list[Row]]:
    branches: list[Row] = [
        {
            "branch_id": f"BR-FX-{i}",
            "branch_code": f"B{i:04d}",
            "branch_name": f"Sucursal Prueba {i}",
            "branch_type": "Main",
            "address": "Calle Falsa 123",
            "city": "Bogotá",
            "state": "Cundinamarca",
            "country": "Colombia",
            "geographic_zone": "Urbana",
            "phone": "+57 1 0000 0000",
            "email": f"branch{i}@example.test",
            "opening_time": "08:00:00",
            "closing_time": "17:00:00",
            "has_atms": "True",
            "atm_count": "2",
            "has_teller_windows": "True",
            "teller_window_count": "3",
            "latitude": "4.6",
            "longitude": "-74.0",
            "branch_opening_date": "2020-01-01",
            "branch_status": "Active",
        }
        for i in (1, 2)
    ]
    customers: list[Row] = []
    for i in (1, 2, 3):
        customers.append(
            {
                "customer_id": f"CUST-FX-{i:03d}",
                "document_number": f"1000000{i}",
                "document_type": "CC",
                "first_name": "Persona",
                "last_name": "Prueba Sintética",
                "date_of_birth": "1990-01-01",
                "gender": "F",
                "email": f"persona{i}@example.test",
                "mobile_phone": "+57 300 000 0000",
                "landline_phone": None,
                "address": "Calle Falsa 123",
                "city": "Bogotá",
                "state": "Cundinamarca",
                "country": "Colombia",
                "postal_code": "110111",
                "detected_accent": "colombian",
                "segment": "Basic",
                "credit_score": "700",
                "estimated_monthly_income": "2500000.00",
                "occupation": "Engineer",
                "marital_status": "Single",
                "education_level": "University",
                "registration_date": "2022-01-01 10:00:00",
                "registration_branch_id": "BR-FX-1",
                "customer_status": "Active",
                "last_updated": "2026-01-01 10:00:00",
                "accepts_marketing": "True",
            }
        )
    customers[1]["document_number"] = " 10000002 "  # hashed after trimming
    customers[1]["credit_score"] = "900"  # out of range (300-850)
    customers[2]["segment"] = None  # NOT NULL violation
    customers[2]["registration_branch_id"] = "BR-FX-404"  # orphan (warn test)
    customers.append({**customers[0], "customer_id": None})  # no business key: excluded

    products: list[Row] = [
        {
            "product_id": f"PRD-FX-{i:03d}",
            "customer_id": f"CUST-FX-{i:03d}",
            "product_type": "Tarjeta Crédito",
            "product_number": f"45000000000{i:05d}",
            "currency": "COP",
            "current_balance": "1000.00",
            "credit_limit": "5000000.00",
            "interest_rate": "25.50",
            "opening_date": "2023-01-01",
            "expiration_date": "2028-01-01",
            "opening_branch_id": "BR-FX-1",
            "product_status": "Active",
            "opening_channel": "Branch",
            "has_linked_app": "True",
            "days_past_due": "0",
            "last_transaction_date": _ts(0),
            "last_updated": "2026-01-01 10:00:00",
        }
        for i in (1, 2, 3)
    ]
    products += [
        dict(products[1]),  # PRD-FX-002 twice in bronze: same key, not a number collision
        {
            **products[1],
            "product_id": "PRD-FX-004",
            "product_type": "Tarjeta Débito",
            "product_number": "4500-0000-0000-0042",  # separators stripped for card_last4
        },
        {
            **products[2],
            "product_id": "PRD-FX-005",
            "product_type": "Cuenta Ahorro",  # not a card: card_type NULL
            "product_number": products[2]["product_number"],  # collides with PRD-FX-003
        },
    ]
    agents: list[Row] = [
        {
            "agent_id": f"AGT-FX-{i:02d}",
            "employee_code": f"E{i:05d}",
            "first_name": "Agente",
            "last_name": "Prueba Uno",
            "email": f"agent{i}@example.test",
            "phone": None,
            "native_accent": "colombian",
            "country_of_origin": "Colombia",
            "assigned_branch_id": "BR-FX-1",
            "agent_type": "Phone",
            "experience_level": "Senior",
            "languages": "español, portugués",
            "specialty": "Fraudes",
            "hire_date": "2021-01-01",
            "avg_csat": "4.50",
            "total_monthly_interactions": "300",
            "agent_status": "Active",
            "work_shift": "Morning",
        }
        for i in (1, 2)
    ]
    agents += [
        dict(agents[0]),  # AGT-FX-01 twice in bronze: same key, not a code collision
        {**agents[1], "agent_id": "AGT-FX-03"},  # shares employee_code with AGT-FX-02 (warn)
        # Only defect: unknown branch. Flagged, but not folded into dq_any (DQ_ANY_EXCLUDED).
        {
            **agents[0],
            "agent_id": "AGT-FX-04",
            "employee_code": "E00004",
            "assigned_branch_id": "BR-FX-404",
        },
    ]
    campaigns: list[Row] = [
        {
            "campaign_id": "CMP-FX-001",
            "campaign_name": "campana_prueba_2026",
            "description": "Campaña sintética",
            "campaign_type": "Email",
            "campaign_objective": "Retention",
            "promoted_product": "Tarjeta Crédito",
            "target_segment": "Basic",
            "target_country": "Colombia",
            "start_date": "2026-05-01",
            "end_date": "2026-07-01",
            "budget": "10000.00",
            "campaign_status": "Active",
            "expected_conversion_rate": "2.50",
        }
    ]

    def txn(i: int, **overrides: str | None) -> Row:
        row: Row = {
            "transaction_id": f"TXN-FX-{i:04d}",
            "transaction_date": _ts(i % 3),
            "process_date": _day(i % 3),
            "product_id": "PRD-FX-001",
            "customer_id": "CUST-FX-001",
            "transaction_type": "Purchase",
            "transaction_category": "Food",
            "amount": "40000.00",
            "currency": "COP",
            "amount_usd": "10.00",
            "channel": "POS",
            "branch_id": None,
            "merchant_name": "Tienda Prueba",
            "merchant_category": "Food",
            "transaction_country": "Colombia",
            "transaction_city": "Bogotá",
            "transaction_status": "Approved",
            "response_code": "00",
            "is_fraud": "False",
            "fraud_score": "12.50",
            "latitude": None,
            "longitude": None,
        }
        row.update(overrides)
        return row

    transactions: list[Row] = [
        txn(1),
        txn(2),
        txn(2, fraud_score="13.00"),  # duplicated business key
        txn(3, amount="12,5O"),  # cast failure
        txn(4, fraud_score="150.00"),  # out of range (0-100)
        txn(5, process_date=_day(5)),  # late arrival (process_date after event date)
        txn(6, transaction_status="Bogus"),  # invalid enum
        txn(7, customer_id="CUST-FX-404"),  # orphan customer
        txn(8, transaction_date=_ts(40), process_date=_day(40)),  # no FX rate that day
        txn(9, amount_usd="99.00"),  # amount_usd far from round(amount / 4000, 2)
        txn(
            10,
            currency="USD",
            amount="25.00",
            amount_usd=None,  # USD rows have no amount_usd in the source: not flagged
            is_fraud="True",
            fraud_score="88.00",
        ),
        txn(11, amount="40000.005"),  # lossy: decimal(15,2) would round it silently
        txn(12, transaction_date=f"{_day(0)}T12:00:00Z"),  # offset dropped by the cast
        txn(13, amount_usd=None),  # COP without amount_usd
        # UTC date one day after the last FX date; the local process_date still has a rate
        txn(14, transaction_date=_ts(10, hour=2), process_date=_day(9)),
        txn(15, is_fraud=None),  # missing label: no dq flag
        txn(16, product_id="PRD-FX-002"),  # product of CUST-FX-002 used by CUST-FX-001
        txn(17, product_id="PRD-FX-404"),  # orphan product
        txn(18, is_fraud="maybe"),  # unparseable label: no dq flag
        txn(0, transaction_id=None),  # no business key: excluded
    ]

    interactions: list[Row] = [
        {
            "interaction_id": f"INT-FX-{i:03d}",
            "interaction_date": _ts(0),
            "process_date": _day(0),
            "customer_id": "CUST-FX-001",
            "agent_id": "AGT-FX-01",
            "interaction_type": "Inbound Call",
            "channel": "Phone",
            "contact_reason": "Transaccional",
            "reason_category": "Transaccional",
            "duration_seconds": "300",
            "wait_time_seconds": "30",
            "was_resolved": "True",
            "requires_followup": "False",
            "detected_sentiment": "Neutral",
            "sentiment_score": "0.10",
            "customer_detected_accent": "colombian",
            "agent_used_accent": "colombian",
            "was_escalated": "False",
            "mentioned_products": "PRD-FX-001",
            "has_transcript": "True",
            "has_recording": "True",
        }
        for i in (1, 2)
    ]
    transcripts: list[Row] = [
        {
            "transcript_id": "TRN-FX-001",
            "interaction_id": "INT-FX-001",
            "process_date": _day(0),
            "customer_id": "CUST-FX-001",
            "agent_id": "AGT-FX-01",
            "full_text": "Hola, no reconozco un cargo.",
            "customer_text": "No reconozco un cargo.",
            "agent_text": "Hola.",
            "detected_language": "es",
            "detected_accent": "colombian",
            "accent_confidence": "0.90",
            "detected_keywords": "cargo",
            "mentioned_entities": "{}",
            "detected_intents": "consulta_general",
            "main_topics": "Transaccional",
            "transcription_model": "Whisper v3",
            "audio_quality": "High",
            "duration_seconds": "300",
        }
    ]
    surveys: list[Row] = [
        {
            "survey_id": "SRV-FX-001",
            "survey_date": _ts(1),
            "process_date": _day(1),
            "interaction_id": "INT-FX-001",
            "customer_id": "CUST-FX-001",
            "agent_id": "AGT-FX-01",
            "survey_type": "CSAT",
            "send_channel": "Email",
            "main_score": "4",
            "nps_category": None,
            "question_1_text": "¿Cómo calificaría la atención?",
            "question_1_response": "4",
            "question_2_text": None,
            "question_2_response": None,
            "question_3_text": None,
            "question_3_response": None,
            "open_comments": None,
            "comment_sentiment": None,
            "response_time_hours": "24.00",
            "campaign_response_rate": "30.00",
        }
    ]
    events: list[Row] = [
        {
            "event_id": f"EVT-FX-{i:03d}",
            "event_date": _ts(0),
            "process_date": _day(0),
            "customer_id": "CUST-FX-001" if i == 1 else None,
            "session_id": "SES-FX-001",
            "event_type": "Login",
            "event_category": "Authentication",
            "channel": "Android App",
            "platform": "Android",
            "browser": None,
            "app_version": "1.2.3",
            "page_url": "/login",
            "page_title": "Iniciar Sesión",
            "action": "login",
            "element_id": "login_form",
            "product_id": None,
            "event_value": None,
            "duration_seconds": "5",
            "ip_address": "192.0.2.1",
            "ip_country": "Colombia",
            "ip_city": "Bogotá",
            "is_mobile": "True",
            "referrer": None,
            "utm_source": None,
            "utm_medium": None,
            "utm_campaign": None,
        }
        for i in (1, 2)
    ]
    complaints: list[Row] = [
        {
            "complaint_id": "CMP-FX-101",
            "creation_date": _ts(1),
            "process_date": _day(1),
            "customer_id": "CUST-FX-001",
            "case_type": "Claim",
            "category": "Transactions",
            "subcategory": "Cargo no reconocido",
            "reception_channel": "Call Center",
            "affected_product_id": "PRD-FX-001",
            "related_branch_id": None,
            "origin_interaction_id": None,
            "description": "Cargo no reconocido en tarjeta.",
            "claimed_amount": "40000.00",
            "currency": "COP",
            "priority": "High",
            "status": "Open",
            "assigned_agent_id": "AGT-FX-01",
            "assignment_date": _ts(1, 13),
            "first_response_date": None,
            "resolution_date": None,
            "closing_date": None,
            "sla_breached": "False",
            "resolution_days": None,
            "resolution": None,
            "compensation_granted": None,
            "resolution_satisfaction": None,
            "is_repeat_complainer": "False",
        }
    ]
    sends: list[Row] = [
        {
            "send_id": "SND-FX-001",
            "send_date": _ts(0),
            "process_date": _day(0),
            "campaign_id": "CMP-FX-001",
            "customer_id": "CUST-FX-001",
            "send_channel": "Email",
            "template_used": "tpl_1",
            "subject": "Oferta",
            "send_status": "Sent",
            "was_delivered": "True",
            "was_opened": "True",
            "open_date": _ts(0, 14),
            "was_clicked": "False",
            "click_date": None,
            "click_count": None,
            "had_conversion": "False",
            "conversion_date": None,
            "conversion_value": None,
            "open_device": "Mobile",
            "open_country": "Colombia",
            "failure_reason": None,
            "send_cost": "0.0100",
        }
    ]
    rates: list[Row] = []
    for offset in range(10):  # rates exist only for the first 10 days (TXN 8 falls outside)
        for source in CURRENCIES:
            for target in CURRENCIES:
                if source == target:
                    continue
                rate = float(USD_PER_UNIT[source]) / float(USD_PER_UNIT[target])
                text = f"{rate:.6f}"
                rates.append(
                    {
                        "date": _day(offset),
                        "source_currency": source,
                        "target_currency": target,
                        "exchange_rate": text,
                        "buy_rate": text,
                        "sell_rate": text,
                        "source": "Synthetic",
                    }
                )
    return {
        "customers": customers,
        "products": products,
        "branches": branches,
        "service_agents": agents,
        "marketing_campaigns": campaigns,
        "transactions": transactions,
        "call_center_interactions": interactions,
        "call_transcripts": transcripts,
        "satisfaction_surveys": surveys,
        "digital_events": events,
        "complaints": complaints,
        "campaign_sends": sends,
        "daily_exchange_rates": rates,
    }


def generate_synthetic_bronze(bronze_dir: Path) -> Manifest:
    """Write 13 all-string parquet files and a `data_mode: synthetic` manifest."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    bronze_dir.mkdir(parents=True, exist_ok=True)
    fixed_time = datetime(2026, 6, 17, tzinfo=UTC)
    entries: list[FileEntry] = []
    tables = _rows()
    for table in TABLES:
        columns = COLUMNS[table]
        rows = tables[table]
        schema = pa.schema([pa.field(name, pa.string()) for name in columns])
        data = {name: [row.get(name) for row in rows] for name in columns}
        path = bronze_dir / f"{table}.parquet"
        pq.write_table(pa.table(data, schema=schema), path)
        entries.append(
            FileEntry(
                table=table,
                source_key=f"synthetic/{table}",
                relative_path=path.name,
                row_count=len(rows),
                byte_size=path.stat().st_size,
                sha256=sha256_file(path),
                ingested_at=fixed_time,
            )
        )
    manifest = Manifest(
        data_mode="synthetic",
        aws_profile="none",
        s3_bucket="none",
        started_at=fixed_time,
        finished_at=fixed_time,
        files=tuple(entries),
    )
    write_manifest(manifest, bronze_dir / "_manifest.json")
    return manifest
