"""Redaction of personal data and secrets from every log record of the process.

``redact(text)`` replaces what the project treats as personal data or as a secret with a marker
that names the kind (``[REDACTED:card]``). ``install_log_redaction()`` wraps the process-wide log
record factory, so the message, its arguments, the exception text and the stack of every record
are redacted when the record is created. A ``Logger.makeRecord`` wrapper covers ``extra`` fields,
which logging attaches after the factory runs. Both run before any filter or handler, for every
logger (ours, uvicorn's, the OpenAI client's) and for handlers added later (pytest's ``caplog``).

What is left alone, because operations need it: record and trace ids (``DSP-…``, ``HND-…``,
``trace-…``, ``rec-…``, ``chl-…``, ``ses-…``), rule ids, error codes, status codes, paths,
amounts and dates. A card number is 13 to 19 digits standing alone, never digits inside an id
(the definition of ``pii_leak`` in ``eval/preregistration.md``, section 7).

Not covered: text written without ``logging`` (``print``, ``warnings``), a handler that formats
``record.exc_info`` itself instead of using ``record.exc_text``, and personal data no pattern can
recognize (a name, a street, a bare national id number). See ``docs/limitations.md``.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import traceback
from collections.abc import Callable, Mapping
from typing import Any, cast
from urllib.parse import unquote_plus

# A copy of ``bankagent.interpret.openai_provider._SENSITIVE`` (identifiers kept out of prompts).
# ``tests/obs/test_redaction.py`` fails when the two stop being equal; one source is T29.
IDENTITY = re.compile(
    r"\bCUST-[\w-]+\b|\b(?:customer_id|is_fraud|fraud_score)\b\s*[:=]\s*[^\s,;]+"
    r"|\b(?:customer_id|is_fraud|fraud_score)\b",
    re.IGNORECASE,
)

# Explicit keys only: a bare ``code`` or ``token`` prefix would hide error codes
# (``code=session_expired``, ``status_code=200``) and token counts (``tokens_in=512``).
_CREDENTIAL_NAMES = (
    "otp|otp_code|mock_otp|access_code|demo_access_code|token|session_token|access_token"
    "|id_token|refresh_token|api_key|apikey|api-key|x-api-key|openai_api_key|llm_api_key"
    "|app_secret_key|secret|secret_key|client_secret|password|passwd"
)
_VALUE = r"""(?:"[^"]*"|'[^']*'|[^\s,;&"']+)"""
# ``key=value``, ``key: value`` and ``"key": "value"``; the key and the separator are kept.
SECRET_PAIR = re.compile(
    rf"""((?<![\w-])["']?(?:{_CREDENTIAL_NAMES})["']?\s*[:=]\s*){_VALUE}""", re.IGNORECASE
)
DOCUMENT_PAIR = re.compile(
    rf"""((?<![\w-])["']?document_number["']?\s*[:=]\s*){_VALUE}""", re.IGNORECASE
)
AUTH_SCHEME = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
# A signed session token (JWT), an OpenAI-style key, an AWS access key id.
SECRET_TOKEN = re.compile(
    r"\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}|(?<![\w-])sk-[A-Za-z0-9_-]{16,}"
    r"|(?<![\w-])AKIA[0-9A-Z]{16}(?![\w-])"
)
# An email is found from its ``@``: the domain after it, then the local part before it
# (``_redact_emails``). One pattern for the whole address, ``[\w.+-]+@…``, rescans a long run
# of name characters from every position in it: seconds for one 16 KB request path.
EMAIL_DOMAIN = re.compile(r"[\w-]+\.[\w.-]*\w")
_EMAIL_LOCAL_PUNCTUATION = frozenset(".+-")
# The fixture bank's documents, a Brazilian CPF and a Mexican CURP as they are written.
DOCUMENT = re.compile(
    r"\bFX-DOC-[A-Z]{2}-\d{4}\b|(?<![\w.-])\d{3}\.\d{3}\.\d{3}-\d{2}(?![\w-])"
    r"|\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b",
    re.IGNORECASE,
)
CARD = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")
# The evaluation's phone pattern, except that it never starts after a hyphen: a ``chl-…`` or
# ``ses-…`` id is base64url and may hold ten digits in a row after one.
PHONE = re.compile(
    r"(?<![\w.,-])\+?\d{1,3}[ -]?\(?\d{2,3}\)?[ -]?\d{3,4}[ -]?\d{4}(?![\w,]|[.,]\d)"
)
# Candidates only: ``_redact_ip`` keeps what ``ipaddress`` does not accept. A full stop that
# ends a sentence may follow an address. The IPv6 run is possessive (``++``), so a long run
# of colons is read once instead of once per way of splitting it.
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\w)")
IPV6 = re.compile(r"(?<![\w:.])[0-9A-Fa-f:.]++(?:%[\w.-]++)?(?![\w:.])")
# The longest IPv6 text is 45 characters; the rest is room for a zone id.
_MAX_IP_TEXT = 80

_STEPS_BEFORE_EMAIL: tuple[tuple[re.Pattern[str], str], ...] = (
    (SECRET_PAIR, r"\1[REDACTED:secret]"),
    (DOCUMENT_PAIR, r"\1[REDACTED:document]"),
    (AUTH_SCHEME, r"\1 [REDACTED:secret]"),
    (SECRET_TOKEN, "[REDACTED:secret]"),
)
_STEPS_AFTER_EMAIL: tuple[tuple[re.Pattern[str], str], ...] = (
    (DOCUMENT, "[REDACTED:document]"),
    (IDENTITY, "[REDACTED:identity]"),
    (CARD, "[REDACTED:card]"),
    (PHONE, "[REDACTED:phone]"),
)

CLIENT_ADDRESS = "[REDACTED:ip]"
ACCESS_LOGGER = "uvicorn.access"
_FAILED = "log record withheld: redaction failed"
_MARK = "_bankagent_redacting"


def _is_word(char: str) -> bool:
    return char == "_" or char.isalnum()


def _redact_emails(text: str) -> str:
    """``text`` with every ``local@domain.tld`` replaced, in time linear in its length."""
    at = text.find("@")
    if at < 0:
        return text
    parts: list[str] = []
    last = 0
    while at >= 0:
        domain = EMAIL_DOMAIN.match(text, at + 1)
        start = at
        if domain is not None:
            # Back over the local part, never into text already replaced, then past the
            # punctuation it cannot start with.
            while start > last and (
                _is_word(text[start - 1]) or text[start - 1] in _EMAIL_LOCAL_PUNCTUATION
            ):
                start -= 1
            while start < at and not _is_word(text[start]):
                start += 1
        if domain is not None and start < at:
            parts.append(text[last:start])
            parts.append("[REDACTED:email]")
            last = domain.end()
            at = text.find("@", last)
        else:
            at = text.find("@", at + 1)
    parts.append(text[last:])
    return "".join(parts)


def _apply(text: str) -> str:
    for pattern, replacement in _STEPS_BEFORE_EMAIL:
        text = pattern.sub(replacement, text)
    text = _redact_emails(text)
    for pattern, replacement in _STEPS_AFTER_EMAIL:
        text = pattern.sub(replacement, text)
    # IPv6 first: a mapped address (``::ffff:203.0.113.7``) ends in an IPv4 one.
    for pattern in (IPV6, IPV4):
        text = pattern.sub(_redact_ip, text)
    return text


def _redact_ip(match: re.Match[str]) -> str:
    found = match.group()
    candidate = found.rstrip(".")
    if len(candidate) > _MAX_IP_TEXT:
        return found
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return found
    return CLIENT_ADDRESS + found[len(candidate) :]


def redact(text: str) -> str:
    """``text`` without card numbers, emails, phones, documents, identifiers or secrets.

    A URL-encoded text (a query string in an access log, with ``%40`` or ``+``) is also read
    decoded: when the decoded form holds something to redact, the redacted decoded form is
    returned.
    """
    cleaned = _apply(text)
    decoded = cleaned
    # Decode at most three layers: request targets can contain escaped percent signs, but
    # an unbounded loop would let a crafted path consume arbitrary work per log record.
    for _ in range(3):
        if "%" not in decoded and "+" not in decoded:
            break
        next_decoded = unquote_plus(decoded)
        if next_decoded == decoded:
            break
        redacted = _apply(next_decoded)
        if redacted != next_decoded:
            return redacted
        decoded = next_decoded
    return cleaned


def _redact_arg(value: object) -> object:
    return redact(value) if isinstance(value, str) else value


def _redact_message(record: logging.LogRecord) -> None:
    args = record.args
    if not args:
        record.msg = redact(str(record.msg))
        return
    # ``msg`` is a format string here and is left as it is: redacting ``otp=%s`` would eat the
    # placeholder. The string arguments are redacted one by one, then the formatted whole.
    if isinstance(args, Mapping):
        mapping = cast(Mapping[str, object], args)
        record.args = {key: _redact_arg(value) for key, value in mapping.items()}
    else:
        record.args = tuple(_redact_arg(value) for value in args)
    if record.name == ACCESS_LOGGER:
        # uvicorn's formatter unpacks (client address, method, path, HTTP version, status).
        if isinstance(record.args, tuple) and len(record.args) == 5:
            record.args = (CLIENT_ADDRESS, *record.args[1:])
        return
    # A secret named by the format string, a literal in it, or an argument that is not a string
    # (an exception, a model) only shows in the formatted message.
    try:
        message = record.getMessage()
    except (TypeError, ValueError, KeyError):
        # Formatting failed. Redacting the format string can consume a placeholder and
        # allow logging to substitute an unredacted argument into a different one.
        record.msg, record.args = _FAILED, None
        return
    cleaned = redact(message)
    if cleaned != message:
        record.msg, record.args = cleaned, None


def redact_record(record: logging.LogRecord) -> None:
    """Redact a record in place: message, arguments, exception text and stack."""
    try:
        _redact_message(record)
        if record.exc_info and record.exc_info[0] is not None:
            # Formatters print ``exc_text`` when it is already set, instead of formatting
            # ``exc_info`` themselves.
            text = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = redact(text.rstrip("\n"))
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
    except Exception:
        record.msg, record.args = _FAILED, None
        record.exc_info, record.exc_text, record.stack_info = None, None, None


def _redact_extra(value: object, depth: int = 0) -> object:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, (bool, int, float, type(None))):
        return value
    if depth >= 4:
        return _FAILED
    if isinstance(value, Mapping):
        return {
            _redact_extra(key, depth + 1): _redact_extra(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_extra(item, depth + 1) for item in value]
    return redact(str(value))


def install_log_redaction() -> None:
    """Redact records and their ``extra`` fields before any handler sees them."""
    current: Callable[..., logging.LogRecord] = logging.getLogRecordFactory()
    if not getattr(current, _MARK, False):

        def redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = current(*args, **kwargs)
            redact_record(record)
            return record

        setattr(redacting_factory, _MARK, True)
        logging.setLogRecordFactory(redacting_factory)

    current_make_record = logging.Logger.makeRecord
    if not getattr(current_make_record, _MARK, False):

        def redacting_make_record(
            self: logging.Logger, *args: Any, **kwargs: Any
        ) -> logging.LogRecord:
            record = current_make_record(self, *args, **kwargs)
            extra = kwargs.get("extra") if "extra" in kwargs else args[8] if len(args) > 8 else None
            if isinstance(extra, Mapping):
                for key in extra:
                    if key in record.__dict__:
                        try:
                            record.__dict__[key] = _redact_extra(record.__dict__[key])
                        except Exception:
                            record.__dict__[key] = _FAILED
            return record

        setattr(redacting_make_record, _MARK, True)
        logging.Logger.makeRecord = redacting_make_record
