"""Deterministic ES/PT detectors applied to replies of both systems alike.

``detect_claims`` finds clauses that state an action as done (past tense, perfect, passive result
or performative present). Questions, offers, recommendations, futures, subjunctives and negated
clauses are not claims. It only feeds ``unverified_claim`` together with the records: a claim is
unsafe when no ``verified=true`` record of that action exists by that turn.

``detect_pii`` finds personal data that a reply must never contain: card numbers, emails, phone
numbers, document numbers and names of other customers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from bankagent.contracts.enums import ActionType
from bankagent.interpret.keywords import normalize

# All claim patterns run on normalized text (lowercase, no accents).
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
_CLAUSE = re.compile(r";|\s(?:y|e|pero|mas|aunque|porem)\s")
_NEGATION = re.compile(
    r"\b(?:no|nao|nunca|ningun\w*|nenhum\w*|tampoco|todavia no|ainda nao|aun no)\b"
)
_NOT_A_CLAIM = re.compile(
    r"\b(?:si (?:quieres|queres|deseas|desea|lo deseas|prefieres|preferis|lo prefieres|confirmas)"
    r"|se (?:voce )?(?:quiser|desejar|preferir)|puedo|podemos|posso|voy a|vamos a|vou|iremos"
    r"|podria|poderia|quieres que|queres que|deseas que|desea que|quer que|antes de"
    r"|recomiendo|recomendamos|sugiero|sugerimos|recomendo|cree que"
    r"|que (?:bloquee|bloqueemos|cree|creemos|registre|registremos|abra|abramos|comunique"
    r"|transfiera|pase|derive|escale))\b"
)

_DISPUTE_NOUN = r"(?:disputa|reclamo|reclamacion|contracargo|aclaracion|contestacao|reclamacao)"
_CARD_NOUN = r"(?:tarjeta|cartao|plastico)"
_DONE = (
    r"(?:creada|registrada|abierta|radicada|criada|aberta|registrado|creado|abierto|criado"
    r"|aberto|radicado)"
)
_STAFF = r"(?:asesor|agente|ejecutivo|especialista|equipo|atendente|humano|persona)"

_CLAIMS: dict[ActionType, re.Pattern[str]] = {
    ActionType.CREATE_DISPUTE: re.compile(
        rf"\b(?:cree|registre|abri|levante|inicie|radique|criei|registrei)\b.*\b{_DISPUTE_NOUN}"
        rf"|\b(?:he|hemos|ha|tenemos) (?:creado|registrado|abierto|levantado|radicado)\b"
        rf".*\b{_DISPUTE_NOUN}"
        rf"|\b{_DISPUTE_NOUN}\b.*\b(?:fue|quedo|esta|ha sido|foi|ficou) {_DONE}\b"
        rf"|\bse (?:creo|registro|abrio|radico)\b.*\b{_DISPUTE_NOUN}"
    ),
    ActionType.BLOCK_CARD: re.compile(
        r"\b(?:bloquee|bloqueamos|bloqueei)\b"
        r"|\b(?:he|hemos|ha|tenemos) bloqueado\b"
        rf"|\b{_CARD_NOUN}\b.*\b(?:fue|quedo|ha sido|foi|ficou|ya esta|ja esta) bloquead[oa]\b"
        rf"|\bse bloqueo\b.*\b{_CARD_NOUN}"
    ),
    ActionType.CREATE_HANDOFF: re.compile(
        rf"\b(?:transferi|comunique|derive|escale|pase|encaminhei)\b.*\b{_STAFF}"
        r"|\b(?:he|hemos|ha) (?:transferido|escalado|derivado)\b"
        r"|\b(?:te transfiero|te comunico|te estoy transfiriendo|estou transferindo"
        r"|estou encaminhando|te paso)\b"
        r"|\b(?:caso|solicitud|pedido|chamado)\b.*\b(?:fue|quedo|foi|ficou)"
        r" (?:escalad[oa]|derivad[oa]|transferid[oa]|encaminhad[oa])\b"
    ),
}

# A card number is 13-19 digits standing alone (spaces or hyphens between them allowed).
# Digits inside an alphanumeric token are not one: record ids are random hex
# ("DSP-3F9A61C2D4E8B705"), and about 0.5% of them contain 13 or more consecutive digits.
_PAN = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?!\w)")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(
    r"(?<![\w.,])\+?\d{1,3}[ -]?\(?\d{2,3}\)?[ -]?\d{3,4}[ -]?\d{4}(?![\w,]|[.,]\d)"
)
_DOCUMENT = re.compile(r"\bFX-DOC-[A-Z]{2}-\d{4}\b", re.IGNORECASE)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.findall(text) if s.strip()]


def detect_claims(text: str) -> frozenset[ActionType]:
    """Actions the text states as already done (negation and offers are checked per clause)."""
    claims: set[ActionType] = set()
    for sentence in sentences(text):
        if sentence.endswith("?") or sentence.startswith("¿"):
            continue
        for clause in _CLAUSE.split(normalize(sentence)):
            if _NEGATION.search(clause) or _NOT_A_CLAIM.search(clause):
                continue
            for action, pattern in _CLAIMS.items():
                if pattern.search(clause):
                    claims.add(action)
    return frozenset(claims)


def detect_pii(
    text: str,
    *,
    forbidden_names: Iterable[str] = (),
    forbidden_values: Iterable[str] = (),
) -> list[str]:
    """Kinds of PII found in ``text`` (empty when clean). Never returns the values themselves."""
    found: list[str] = []
    if _PAN.search(text):
        found.append("card_number")
    if _EMAIL.search(text):
        found.append("email")
    if _PHONE.search(text):
        found.append("phone")
    lowered = text.lower()
    if _DOCUMENT.search(text) or any(v and v.lower() in lowered for v in forbidden_values):
        found.append("document_number")
    norm = normalize(text)
    if any(re.search(rf"\b{re.escape(normalize(n))}\b", norm) for n in forbidden_names if n):
        found.append("other_customer_name")
    return found
