"""Log redaction (T20): what each pattern removes, what it must leave, and the record factory."""

from __future__ import annotations

import base64
import io
import logging
import os
import random

import pytest

from bankagent.eval.detectors import detect_pii
from bankagent.interpret import openai_provider
from bankagent.obs import install_log_redaction, redact
from bankagent.obs.redaction import ACCESS_LOGGER, CLIENT_ADDRESS, IDENTITY

CARD = "4111111111111111"
EMAIL = "ana.perez@example.com"
# Built at import time: shaped like a signed session token, and not a literal credential.
JWT = ".".join(
    base64.urlsafe_b64encode(part).rstrip(b"=").decode()
    for part in (b'{"alg":"HS256"}', b'{"sid":"ses-abc"}', b"signature-value")
)
KEY = "sk-" + "proj-" + "Ab3dE5gH7jK9mN1pQ3sT"
AWS_KEY_ID = "AKIA" + "IOSFODNN7" + "EXAMPLE"

# 20,000 ids of each kind by default; `T20_ID_SAMPLES=200000` for the run in the decision ledger.
ID_SAMPLES = int(os.environ.get("T20_ID_SAMPLES", "20000"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"card {CARD} seen", "card [REDACTED:card] seen"),
        ("4111 1111 1111 1111", "[REDACTED:card]"),
        ("4111-1111-1111-1111.", "[REDACTED:card]."),
        ("amex 378282246310005", "amex [REDACTED:card]"),
        ("thirteen 4222222222222", "thirteen [REDACTED:card]"),
        ("nineteen 6304000000000000000", "nineteen [REDACTED:card]"),
        (f"mail {EMAIL}, thanks", "mail [REDACTED:email], thanks"),
        ("a+b@banco.com.mx", "[REDACTED:email]"),
        ("llámame al +52 55 1234 5678", "llámame al [REDACTED:phone]"),
        ("tel 11 98765-4321", "tel [REDACTED:phone]"),
        ("cel +573001234567", "cel [REDACTED:phone]"),
        ("doc FX-DOC-MX-0001", "doc [REDACTED:document]"),
        ("cpf 123.456.789-09", "cpf [REDACTED:document]"),
        ("curp GOMC850101HDFRRL09", "curp [REDACTED:document]"),
        ("document_number=30111222", "document_number=[REDACTED:document]"),
        ("customer CUST-MX-0001", "customer [REDACTED:identity]"),
        ("customer_id=abc-123 ok", "[REDACTED:identity] ok"),
        ("fraud_score: 0.93", "[REDACTED:identity]"),
        ("otp=482913", "otp=[REDACTED:secret]"),
        ("otp_code: 482913", "otp_code: [REDACTED:secret]"),
        ('{"mock_otp": "482913", "ok": true}', '{"mock_otp": [REDACTED:secret], "ok": true}'),
        ("access_code=jueces-demo-2026", "access_code=[REDACTED:secret]"),
        ("DEMO_ACCESS_CODE='abc def'", "DEMO_ACCESS_CODE=[REDACTED:secret]"),
        ("/x?token=abc.def&page=2", "/x?token=[REDACTED:secret]&page=2"),
        ("OPENAI_API_KEY=whatever", "OPENAI_API_KEY=[REDACTED:secret]"),
        ("password: hunter2;", "password: [REDACTED:secret];"),
        (f"Authorization: Bearer {JWT}", "Authorization: Bearer [REDACTED:secret]"),
        (f"session token {JWT} issued", "session token [REDACTED:secret] issued"),
        (f"key {KEY} rejected", "key [REDACTED:secret] rejected"),
        (f"id {AWS_KEY_ID}", "id [REDACTED:secret]"),
    ],
)
def test_personal_data_and_secrets_are_replaced_by_a_marker(text: str, expected: str) -> None:
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "code=session_expired",
        "status_code=200",
        "error=invalid_credentials reason=access_code",
        "tokens_in=512 tokens_out=128 cost_usd=0.000213",
        "login_rejected reason=challenge_exhausted challenge=chl-0123456789012345678901ab",
        "session_issued session=ses-98765432109876543210_- challenge=chl-A-5512345678-zz",
        "readiness_failed dependency=serving_db error=OperationalError",
        "dispute DSP-68EA194969CC12E4 created",
        "dispute DSP-1234567890123456 handoff HND-0000000000000000",
        "trace-12345678901234567890123456789012",
        "rec-0123456789abcdef0123456789abcdef-t3-s12",
        "rule DSP-ESC-02 fired, policy_version=1.0.0",
        "amount=1250000.50 MXN",
        "monto $ 2.450,50 y 85.900",
        "card_last4=4242",
        "2026-10-03 17:06:10,123 started",
        "2026-10-03T17:06:55+00:00",
        "Started server process [12345]",
        'POST /api/chat/turn HTTP/1.1" 200',
        "latency_ms=1234.5 turn_index=12",
        "model=gpt-6-luna prompt_version=interpret-v2",
        "Secret(***)",
        "took 100% of the budget",
    ],
)
def test_what_operations_need_is_left_alone(text: str) -> None:
    assert redact(text) == text


def test_redaction_is_idempotent() -> None:
    line = f"otp=482913 {EMAIL} {CARD} +52 55 1234 5678 Bearer {JWT} CUST-MX-0001"
    once = redact(line)
    assert redact(once) == once


@pytest.mark.parametrize(
    ("text", "markers"),
    [
        ("/api/chat/turn?mail=ana.perez%40example.com", ["[REDACTED:email]"]),
        ("/x?q=mi%20tarjeta%204111%201111%201111%201111", ["[REDACTED:card]"]),
        ("/x?q=tarjeta+4111+1111+1111+1111&otp=482913", ["[REDACTED:card]", "[REDACTED:secret]"]),
    ],
)
def test_a_percent_encoded_text_is_read_decoded(text: str, markers: list[str]) -> None:
    cleaned = redact(text)
    assert all(marker in cleaned for marker in markers)
    assert "4111" not in cleaned
    assert "example.com" not in cleaned
    assert "482913" not in cleaned


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("client=203.0.113.7", "client=[REDACTED:ip]"),
        ("client=[2001:db8::1]", "client=[[REDACTED:ip]]"),
        ("/health?client_ip=203.0.113.7", "/health?client_ip=[REDACTED:ip]"),
        (
            "Uvicorn running on http://0.0.0.0:10000",
            "Uvicorn running on http://[REDACTED:ip]:10000",
        ),
        ("2026-10-03 17:06:10,123 started", "2026-10-03 17:06:10,123 started"),
    ],
)
def test_ip_literals_are_redacted_without_changing_timestamps(text: str, expected: str) -> None:
    assert redact(text) == expected


@pytest.mark.parametrize(
    ("text", "marker"),
    [
        ("/health?mail=ana.perez%2540example.com", "[REDACTED:email]"),
        ("/x?otp%253D482913", "[REDACTED:secret]"),
        ("/x?ip=203%252E0%252E113%252E7", "[REDACTED:ip]"),
    ],
)
def test_double_encoded_query_values_are_redacted(text: str, marker: str) -> None:
    assert marker in redact(text)


def _random_ids(rng: random.Random) -> list[str]:
    def urlsafe(size: int) -> str:
        return base64.urlsafe_b64encode(rng.randbytes(size)).rstrip(b"=").decode()

    hex16 = f"{rng.getrandbits(64):016X}"
    hex32 = f"{rng.getrandbits(128):032x}"
    return [
        f"DSP-{hex16}",
        f"HND-{hex16}",
        f"BLK-{hex16}",
        f"trace-{hex32}",
        f"rec-{hex32}",
        f"idem-{hex32}",
        f"trace-{hex32}-t{rng.randrange(40)}-s{rng.randrange(40)}",
        f"chl-{urlsafe(18)}",
        f"ses-{urlsafe(18)}",
    ]


def test_random_record_and_trace_ids_are_never_redacted() -> None:
    rng = random.Random(20)
    changed: list[str] = []
    for _ in range(ID_SAMPLES):
        for value in _random_ids(rng):
            line = f"event id={value} done"
            if redact(value) != value or redact(line) != line:
                changed.append(value)
    assert changed == []


def test_the_identity_pattern_is_the_one_that_keeps_identifiers_out_of_prompts() -> None:
    # Two copies until T29 unifies them: a change to either must be made to both.
    provider = openai_provider._SENSITIVE
    assert provider.pattern == IDENTITY.pattern
    assert provider.flags == IDENTITY.flags


@pytest.mark.parametrize(
    "text",
    [
        f"mi tarjeta es {CARD}",
        "la tarjeta 4111 1111 1111 1111 no es mía",
        "cartão 5555-5555-5555-4444",
        f"escríbeme a {EMAIL}",
        "mi número es +52 55 1234 5678",
        "ligue para 11 98765-4321",
        "documento FX-DOC-CO-0002",
        f"{EMAIL} / {CARD} / +57 300 123 4567 / FX-DOC-AR-0003",
    ],
)
def test_what_the_evaluation_calls_a_leak_is_gone_after_redaction(text: str) -> None:
    # `pii_leak` (eval/preregistration.md, section 7) keeps its own patterns: production code
    # never imports `bankagent.eval`. This ties the two definitions together.
    assert detect_pii(text)
    assert detect_pii(redact(text)) == []


# -- the record factory ---------------------------------------------------------


class _Carrier:
    """An argument that is not a string and only shows its content when formatted."""

    def __str__(self) -> str:
        return f"turn(text='tarjeta {CARD}, correo {EMAIL}')"


def _clean(text: str) -> bool:
    return CARD not in text and EMAIL not in text and "482913" not in text


def test_without_the_factory_a_record_keeps_what_it_was_given(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        logging.getLogger("bankagent.obs.test").info("card %s", CARD)
    assert CARD in caplog.text


def test_the_message_and_its_arguments_are_redacted_for_any_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    install_log_redaction()
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("bankagent.orchestrator.deep.child").info(f"plain {CARD} {EMAIL}")
        logging.getLogger("openai._base_client").debug("body=%s otp=%s", f"mail {EMAIL}", "482913")
        logging.getLogger("some.library").warning("turn %(text)s", {"text": f"card {CARD}"})
        logging.getLogger().error("object %s", _Carrier())
    assert len(caplog.records) == 4
    assert _clean(caplog.text)
    assert all(_clean(record.getMessage()) for record in caplog.records)
    assert caplog.text.count("[REDACTED:card]") == 3
    assert "[REDACTED:email]" in caplog.text


def test_arguments_keep_their_shape_and_types(caplog: pytest.LogCaptureFixture) -> None:
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("bankagent.api").info("status=%d latency=%.1f id=%s", 200, 12.5, "DSP-1")
    (record,) = caplog.records
    assert record.args == (200, 12.5, "DSP-1")
    assert record.getMessage() == "status=200 latency=12.5 id=DSP-1"


def test_the_exception_text_and_the_stack_are_redacted(caplog: pytest.LogCaptureFixture) -> None:
    install_log_redaction()
    log = logging.getLogger("uvicorn.error.test")
    with caplog.at_level(logging.INFO):
        try:
            try:
                raise ValueError(f"cannot read {CARD}")
            except ValueError as inner:
                raise RuntimeError(f"turn failed for {EMAIL}") from inner
        except RuntimeError:
            log.exception("Exception in ASGI application")
        log.info("where", stack_info=True)
    failed = caplog.records[0]
    assert failed.exc_text is not None
    assert "RuntimeError: turn failed for [REDACTED:email]" in failed.exc_text
    assert "ValueError: cannot read [REDACTED:card]" in failed.exc_text
    assert "Traceback (most recent call last)" in failed.exc_text
    assert _clean(caplog.text)


def test_an_access_record_keeps_uvicorns_five_arguments_and_loses_the_client_address(
    caplog: pytest.LogCaptureFixture,
) -> None:
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger(ACCESS_LOGGER).info(
            '%s - "%s %s HTTP/%s" %d',
            "203.0.113.7:51234",
            "GET",
            f"/api/chat/transactions?mail={EMAIL}",
            "1.1",
            200,
        )
    (record,) = caplog.records
    assert record.args == (
        CLIENT_ADDRESS,
        "GET",
        "/api/chat/transactions?mail=[REDACTED:email]",
        "1.1",
        200,
    )
    assert "203.0.113.7" not in caplog.text


def test_installing_twice_wraps_once() -> None:
    install_log_redaction()
    factory = logging.getLogRecordFactory()
    make_record = logging.Logger.makeRecord
    install_log_redaction()
    assert logging.getLogRecordFactory() is factory
    assert logging.Logger.makeRecord is make_record


def test_extra_fields_are_redacted_before_a_structured_handler_sees_them() -> None:
    install_log_redaction()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(email)s %(payload)s"))
    logger = logging.getLogger("bankagent.obs.extra_test")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        logger.info(
            "event",
            extra={"email": EMAIL, "payload": {"otp": "otp=482913", "card": CARD}},
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    output = stream.getvalue()
    assert EMAIL not in output
    assert CARD not in output
    assert "482913" not in output
    assert "[REDACTED:email]" in output
    assert "[REDACTED:secret]" in output


def test_a_record_that_cannot_be_redacted_is_withheld(caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        def __str__(self) -> str:
            raise RuntimeError(CARD)

    install_log_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("bankagent.obs.test").info(Broken())
    (record,) = caplog.records
    assert record.getMessage() == "log record withheld: redaction failed"


def test_a_malformed_format_cannot_reinsert_a_short_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("bankagent.obs.test").info("otp=%s %s", "482913")
    (record,) = caplog.records
    assert record.getMessage() == "log record withheld: redaction failed"
    assert record.args is None
    assert "482913" not in caplog.text
