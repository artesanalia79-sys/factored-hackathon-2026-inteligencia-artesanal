"""Deterministic ES/PT keyword interpreter.

Used by the ``StubProvider`` (tests, CI, offline demo) and as the **keyword-router baseline** in the
evaluation. It is intentionally simple and transparent: every decision is a visible rule.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from bankagent.contracts.decisions import DisputeSlots, InterpretationResult
from bankagent.contracts.enums import Dialect, DialogueAct, Intent, Language

MODEL_NAME = "stub-keywords"
PROMPT_VERSION = "stub-v1"


def normalize(text: str) -> str:
    """Lowercase, strip accents and collapse whitespace (``"Não"`` -> ``"nao"``)."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped).strip()


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


# All patterns run on normalized text (lowercase, no accents).
# ---------------------------------------------------------------------------
# Language and dialect
# ---------------------------------------------------------------------------
_PT_MARKERS = _rx(
    r"\bnao\b",
    r"\bvoce\b",
    r"\bcartao\b",
    r"\bcobranca\b",
    r"\breconheco\b",
    r"\bfui eu\b",
    r"\bmeu\b",
    r"\bminha\b",
    r"\bobrigad[oa]\b",
    r"\bola\b",
    r"\bestou\b",
    r"\bquero\b",
    r"\bcompra que\b",
    r"\bchegou\b",
    r"\batendente\b",
    r"\bbloqueie\b",
    r"\bduas vezes\b",
    r"\bsim\b",
    r"\bisso\b",
    r"\breais\b",
)
_ES_MARKERS = _rx(
    r"\btarjeta\b",
    r"\bcargo\b",
    r"\breconozco\b",
    r"\bfui yo\b",
    r"\bmi\b",
    r"\bhola\b",
    r"\bgracias\b",
    r"\bquiero\b",
    r"\bcobro\b",
    r"\busted\b",
    r"\bvos\b",
    r"\bllego\b",
    r"\bdos veces\b",
    r"\bsi\b",
    r"\bpesos\b",
    r"\basesor\b",
)
_ES_AR = _rx(
    r"\bvos\b",
    r"\btenes\b",
    r"\bqueres\b",
    r"\bpodes\b",
    r"\bsabes\b",
    r"\bche\b",
    r"\bfijate\b",
    r"\bdecime\b",
    r"\bhace\b(?= el favor| algo)",
    r"\bbloqueame\b",
    r"\bayudame\b",
    r"\bmira vos\b",
)
_ES_CO = _rx(
    r"\bparce\b",
    r"\bhagale\b",
    r"\bque pena con usted\b",
    r"\bsumerce\b",
    r"\bbacano\b",
    r"\bde una\b",
    r"\bregaleme\b",
)
_ES_MX = _rx(
    r"\bwey\b",
    r"\bguey\b",
    r"\borale\b",
    r"\bchido\b",
    r"\bneta\b",
    r"\bahorita\b",
    r"\bmande\b",
    r"\bplatica\b",
)
_PT_OTHER = _rx(r"\btelemovel\b", r"\bestou a \w+ar\b", r"\bmultibanco\b", r"\bpequeno-almoco\b")

# ---------------------------------------------------------------------------
# Intents (checked in priority order)
# ---------------------------------------------------------------------------
_ATTACK = _rx(
    r"ignor\w* (?:\w+ ){0,3}instruc",
    r"olvid\w* (?:\w+ ){0,3}instrucciones",
    r"esquec\w* (?:\w+ ){0,3}instruc",
    r"system prompt",
    r"prompt del sistema",
    r"prompt do sistema",
    r"instrucciones del sistema",
    r"you are now",
    r"ahora eres",
    r"agora voce e",
    r"developer mode",
    r"modo desarrollador",
    r"jailbreak",
    r"\botro cliente\b",
    r"\botra persona\b.*\b(?:cuenta|tarjeta|movimientos)\b",
    r"\boutro cliente\b",
    r"\bcustomer_?id\b",
    r"\bcliente_?id\b",
    r"\bid de(?:l)? cliente\b",
    r"\bselect \* from\b",
    r"\bdrop table\b",
    r"\bunion select\b",
    r"<\s*/?\s*(?:system|script)\b",
)
_HUMAN = _rx(
    r"\b(?:hablar|comunicar\w*|pasar\w*) con (?:un|una|el|la|alguien)\b",
    r"\basesor\w*\b",
    r"\bagente (?:humano|real)\b",
    r"\bpersona (?:real|de verdad)\b",
    r"\bun humano\b",
    r"\bfalar com (?:um|uma|alguem|o|a)\b",
    r"\batendente\b",
    r"\bpessoa de verdade\b",
)
_STATUS = _rx(
    r"\b(?:estado|estatus|seguimiento) de (?:mi|la|el) (?:reclamo|disputa|caso|aclaracion|queja)\b",
    r"\bcomo va (?:mi|la|el) (?:reclamo|disputa|caso|aclaracion)\b",
    r"\bstatus d[oa] (?:minha|meu)? ?(?:contestacao|disputa|reclamacao|caso)\b",
    r"\bcomo esta (?:minha|meu) (?:contestacao|disputa|reclamacao|caso)\b",
    r"\bnumero de (?:caso|reclamo|protocolo)\b",
    r"\bya (?:abri|hice|puse) (?:un|el) (?:reclamo|disputa)\b",
)
_DUPLICATE = _rx(
    r"\bdos veces\b",
    r"\bdoble\b",
    r"\bduplicad[oa]s?\b",
    r"\brepetid[oa]s?\b",
    r"\bduas vezes\b",
    r"\bem dobro\b",
    r"\bcobrad[oa] duas\b",
    r"\bdois cargos\b",
    r"\bdos cargos iguales\b",
)
_NOT_RECEIVED = _rx(
    r"\bno (?:me )?(?:llego|llegaron|ha llegado|han llegado)\b",
    r"\bnunca (?:me )?(?:llego|llegaron)\b",
    r"\bno (?:lo |la |los |las )?recibi\b",
    r"\bnao (?:recebi|chegou|chegaram)\b",
    r"\bnunca (?:recebi|chegou)\b",
    r"\bno me entregaron\b",
    r"\bnao foi entregue\b",
)
_UNRECOGNIZED = _rx(
    r"\bno (?:lo |la )?reconozco\b",
    r"\bdesconozco\b",
    r"\bno fui yo\b",
    r"\byo no (?:hice|fui|compre|autorice)\b",
    r"\bno (?:hice|autorice|realice) (?:esa|esta|ese|este|ninguna)\b",
    r"\bno autorice\b",
    r"\bfraude\b",
    r"\bnao reconheco\b",
    r"\bdesconheco\b",
    r"\bnao fui eu\b",
    r"\bnao (?:fiz|autorizei)\b",
    r"\b(?:cargo|cobro|compra|cobranca) "
    r"(?:raro|rara|extrano|extrana|estranh[oa]|desconocid[oa]|desconhecid[oa])\b",
    r"\bme (?:clonaron|hackearon)\b",
)
_CARD_BLOCK = _rx(
    r"\bbloque\w*\b",
    r"\b(?:me )?robaron (?:la|mi) tarjeta\b",
    r"\bperdi (?:la|mi) tarjeta\b",
    r"\bse me perdio (?:la|mi) tarjeta\b",
    r"\b(?:roubaram|perdi) (?:o|meu) cartao\b",
    r"\bcancel\w* (?:la|mi) tarjeta\b",
    r"\bcongel\w* (?:la|mi) tarjeta\b",
)
_GENERIC_DISPUTE = _rx(
    r"\bcargo\b", r"\bcobro\b", r"\bcobraron\b", r"\bcobranca\b", r"\bcobraram\b"
)

# ---------------------------------------------------------------------------
# Dialogue acts (order matters: "no fui yo" before "fui yo", "sí fui yo" before "sí")
# ---------------------------------------------------------------------------
_NOT_RECOGNIZE_ACT = _rx(
    r"\bno fui yo\b",
    r"\bno (?:lo |la )?reconozco\b",
    r"\bdesconozco\b",
    r"\bnao fui eu\b",
    r"\bnao (?:o |a )?reconheco\b",
    r"\bno (?:lo |la )?hice yo\b",
    r"\bno es mi(?:o|a)?\b",
    r"\bnao e meu\b",
)
_RECOGNIZE_ACT = _rx(
    r"\b(?:si|sim),? (?:fui (?:yo|eu)|(?:lo |la |o |a )?reconozco|(?:o |a )?reconheco)\b",
    r"\bfui (?:yo|eu)\b",
    r"\bya (?:me acorde|recuerdo|lo reconozco|la reconozco)\b",
    r"\bahora (?:si )?(?:lo |la )?reconozco\b",
    r"\bagora (?:eu )?(?:lembrei|reconheco)\b",
    r"\b(?:lo|la) reconozco\b",
    r"\b(?:o|a) reconheco\b",
    r"\bsi,? (?:era|es) (?:mio|mia|yo)\b",
    r"\bsim,? (?:era|e) meu\b",
)
_AFFIRM = re.compile(
    r"^(?:si|sim|claro|dale|de una|correcto|confirmo|isso|pode|ok|okay|vale|esta bien|hagale"
    r"|por favor|perfecto|exacto|afirmativo|va|sale|bora|certo)\b"
)
_DENY = re.compile(
    r"^(?:no|nao|nop|nel|para nada|cancel\w*|mejor no|todavia no|ainda nao|negativo)\b"
)

# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------
_TXN_REF = re.compile(r"\btxn-[a-z0-9-]+\b")
_LAST4 = re.compile(
    r"(?:terminad[ao]s?|termina|finalizad[ao]s?|finaliza|final|acaba(?:da)?)\s+(?:(?:en|em)\s+)?"
    r"(\d{4})\b"
    r"|ultimos\s+(?:4|cuatro|quatro)\s+(?:digitos\s+)?(?:son\s+|sao\s+|:\s*)?(\d{4})\b"
    r"|(?:\*{2,}|x{2,}|•{2,})\s*(\d{4})\b"
)
_NUMBER = r"\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_AMOUNT_BEFORE = re.compile(
    rf"(?:r\$|us\$|\$|mxn|cop|ars|usd|brl)\s?({_NUMBER})"
    rf"|(?:cargo|cobro|compra|cobranca|monto|valor|importe|por|de|cobraron|cobraram|cobrou|cobro)\s+(?:de\s+)?\$?\s?({_NUMBER})\b(?!\s*(?:de\s+)?(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre|janeiro|fevereiro|marco|maio|junho|julho|setembro|outubro|novembro|dezembro|dias|días|horas|veces|vezes|cuotas|meses)\b)(?![/-]\d)"
)
_AMOUNT_AFTER = re.compile(
    rf"\b({_NUMBER})\s?(?:pesos|mxn|cop|ars|usd|dolares|dolar|reais|real|brl)\b"
)
_CURRENCY_WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bpesos mexicanos\b|\bmxn\b"), "MXN"),
    (re.compile(r"\bpesos colombianos\b|\bcop\b"), "COP"),
    (re.compile(r"\bpesos argentinos\b|\bars\b"), "ARS"),
    (re.compile(r"\bdolares\b|\bdolar\b|\busd\b|us\$"), "USD"),
    (re.compile(r"\breais\b|\bbrl\b|r\$"), "BRL"),
)
_MERCHANT_VOCABULARY: tuple[tuple[str, str], ...] = (
    ("electromundo", "ELECTROMUNDO"),
    ("amazon", "AMAZON"),
    ("rappi", "RAPPI"),
    ("mercado libre", "MERCADOLIBRE"),
    ("mercadolibre", "MERCADOLIBRE"),
    ("mercado livre", "MERCADOLIBRE"),
    ("starbucks", "STARBUCKS"),
    ("spotify", "SPOTIFY"),
    ("paypal", "PAYPAL"),
    ("netflix", "NETFLIX"),
    ("uber eats", "UBER EATS"),
    ("uber", "UBER"),
    ("didi", "DIDI"),
    ("oxxo", "OXXO"),
    ("walmart", "WALMART"),
    ("liverpool", "LIVERPOOL"),
    ("pedidosya", "PEDIDOSYA"),
    ("pedidos ya", "PEDIDOSYA"),
    ("ifood", "IFOOD"),
    ("hotel", "HOTEL"),
    ("airbnb", "AIRBNB"),
    ("booking", "BOOKING"),
    ("steam", "STEAM"),
    ("gamestore", "GAMESTORE"),
    ("apple", "APPLE"),
    ("google", "GOOGLE"),
    ("cinepolis", "CINEPOLIS"),
    ("exito", "EXITO"),
    ("falabella", "FALABELLA"),
)
# Word-boundary patterns; on a tie in position the earlier vocabulary entry wins ("uber eats").
_KNOWN_MERCHANTS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{re.escape(needle)}"), canonical) for needle, canonical in _MERCHANT_VOCABULARY
)
_MONTHS = (
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|"
    "diciembre|janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|"
    "dezembro"
)
_WEEKDAYS = "lunes|martes|miercoles|jueves|viernes|sabado|domingo|segunda|terca|quarta|quinta|sexta"
_DATE_TEXT = re.compile(
    rf"\b\d{{1,2}} de (?:{_MONTHS})\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    rf"|\b(?:el|la|na|no|o|a) (?:{_WEEKDAYS})(?:-feira)?(?: pasad[oa]| passad[oa])?\b"
    r"|\b(?:anteayer|antier|ayer|hoy|anteontem|ontem|hoje)\b"
    r"|\b(?:la semana pasada|el mes pasado|semana passada|mes passado|esta semana|este mes)\b"
)


def detect_language(norm: str) -> Language:
    pt = len(_PT_MARKERS.findall(norm))
    es = len(_ES_MARKERS.findall(norm))
    return Language.PT if pt > es else Language.ES


def detect_dialect(norm: str, language: Language) -> Dialect:
    if language == Language.PT:
        return Dialect.PT_OTHER if _PT_OTHER.search(norm) else Dialect.PT_BR
    if _ES_AR.search(norm):
        return Dialect.ES_AR
    if _ES_CO.search(norm):
        return Dialect.ES_CO
    if _ES_MX.search(norm):
        return Dialect.ES_MX
    return Dialect.ES_NEUTRAL


def detect_intent(norm: str) -> tuple[Intent, float, bool]:
    """Return (intent, confidence, injection_suspected)."""
    if _ATTACK.search(norm):
        return Intent.ATTACK, 0.9, True
    for pattern, intent in (
        (_HUMAN, Intent.HUMAN_REQUEST),
        (_STATUS, Intent.DISPUTE_STATUS),
        (_DUPLICATE, Intent.DISPUTE_DUPLICATE),
        (_NOT_RECEIVED, Intent.DISPUTE_NOT_RECEIVED),
        (_UNRECOGNIZED, Intent.DISPUTE_UNRECOGNIZED),
        (_CARD_BLOCK, Intent.CARD_BLOCK),
    ):
        if pattern.search(norm):
            return intent, 0.85, False
    if _GENERIC_DISPUTE.search(norm):
        # "tengo un problema con un cargo": probably a dispute, reason unknown.
        return Intent.DISPUTE_UNRECOGNIZED, 0.45, False
    return Intent.OUT_OF_SCOPE, 0.3, False


def detect_dialogue_act(norm: str, intent: Intent, slots: DisputeSlots) -> DialogueAct:
    if intent == Intent.HUMAN_REQUEST:
        return DialogueAct.REQUEST_HUMAN
    if _NOT_RECOGNIZE_ACT.search(norm):
        return DialogueAct.NOT_RECOGNIZE_CHARGE
    if _RECOGNIZE_ACT.search(norm):
        return DialogueAct.RECOGNIZE_CHARGE
    if _DENY.search(norm):
        return DialogueAct.DENY
    if _AFFIRM.search(norm):
        return DialogueAct.AFFIRM
    if intent not in (Intent.OUT_OF_SCOPE, Intent.ATTACK):
        return DialogueAct.NEW_REQUEST
    if slots != DisputeSlots():
        return DialogueAct.PROVIDE_INFO
    return DialogueAct.OTHER


def parse_amount(raw: str) -> Decimal | None:
    """Parse LATAM/US amounts: ``2.450``, ``2,450``, ``1.234,56``, ``1,234.56``, ``9.800.000``."""
    token = raw.replace(" ", "")
    if "." in token and "," in token:
        decimal_sep = "." if token.rfind(".") > token.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        token = token.replace(thousands_sep, "").replace(decimal_sep, ".")
    else:
        sep = "." if "." in token else ("," if "," in token else "")
        if sep:
            parts = token.split(sep)
            if len(parts) > 2 or len(parts[-1]) == 3:
                token = token.replace(sep, "")  # thousands separator
            else:
                token = token.replace(sep, ".")  # decimal separator
    try:
        value = Decimal(token).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def extract_slots(norm: str, *, card_block_requested: bool = False) -> DisputeSlots:
    txn = _TXN_REF.search(norm)
    last4_match = _LAST4.search(norm)
    last4 = next((g for g in last4_match.groups() if g), None) if last4_match else None

    # Remove spans that look like numbers but are not amounts before searching for amounts.
    scrubbed = _TXN_REF.sub(" ", norm)
    if last4_match:
        scrubbed = scrubbed.replace(last4_match.group(0), " ")
    scrubbed = _DATE_TEXT.sub(" ", scrubbed)

    amount: Decimal | None = None
    for pattern in (_AMOUNT_BEFORE, _AMOUNT_AFTER):
        match = pattern.search(scrubbed)
        if match:
            raw = next(g for g in match.groups() if g)
            amount = parse_amount(raw)
            if amount is not None:
                break

    currency = next((code for rx, code in _CURRENCY_WORDS if rx.search(norm)), None)

    merchant: str | None = None
    best = len(norm) + 1
    for needle, canonical in _KNOWN_MERCHANTS:
        found = needle.search(norm)
        if found and found.start() < best:
            merchant, best = canonical, found.start()

    date_match = _DATE_TEXT.search(norm)
    return DisputeSlots(
        amount=amount,
        currency=currency,
        merchant_query=merchant,
        card_last4=last4,
        date_text=date_match.group(0) if date_match else None,
        transaction_ref=txn.group(0).upper() if txn else None,
        card_block_requested=card_block_requested,
    )


def interpret_text(text: str) -> InterpretationResult:
    """Interpret one customer message with transparent keyword rules."""
    norm = normalize(text)
    language = detect_language(norm)
    intent, confidence, injection = detect_intent(norm)
    wants_block = intent == Intent.CARD_BLOCK or bool(_CARD_BLOCK.search(norm))
    slots = extract_slots(norm, card_block_requested=wants_block)
    return InterpretationResult(
        intent=intent,
        dialogue_act=detect_dialogue_act(norm, intent, slots),
        slots=slots,
        language=language,
        dialect=detect_dialect(norm, language),
        confidence=confidence,
        injection_suspected=injection,
        model=MODEL_NAME,
        prompt_version=PROMPT_VERSION,
    )
