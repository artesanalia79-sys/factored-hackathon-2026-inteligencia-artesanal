"""Scripted user: sends the case turns, then answers the agent's questions from the FactSheet.

The simulator classifies the agent's question with transparent ES/PT keyword rules. A question
it cannot classify gets the FactSheet default answer (``clarification_answers["default"]`` or a
neutral "not sure") and is counted, so the report shows how often each system asked something
the simulator did not understand. A reply without a question ends the conversation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from bankagent.contracts.enums import Language
from bankagent.contracts.evaluation import EvalCase
from bankagent.interpret.keywords import normalize


class QuestionKind(StrEnum):
    RECOGNIZE = "recognize"
    CONFIRM = "confirm"
    CARD_BLOCK = "card_block"
    HUMAN_OFFER = "human_offer"
    CLARIFY = "clarify"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True, slots=True)
class SimEvent:
    """One simulator answer. ``value`` is the boolean fact given, when the question was yes/no."""

    turn_index: int
    kind: QuestionKind
    value: bool | None
    text: str


# All patterns run on normalized agent text (lowercase, no accents); order matters.
_QUESTION = re.compile(r"[?¿]")
_RECOGNIZE = re.compile(
    r"\breconoc|\breconhec|\bfuiste (?:tu|vos)\b|\bfoi voce\b|\bhiciste (?:tu|vos)\b"
    r"|\bvoce (?:fez|realizou)\b|\bla realizaste\b|\bla hiciste\b"
)
_CONFIRM = re.compile(
    r"\bconfirm|\bprocedo\b|\bpuedo proceder\b|\bposso (?:prosseguir|seguir)\b|\bautoriz"
    r"|\bdeseas continuar\b|\bdeseja continuar\b"
)
# The action a confirmation question names: a dispute (reclamo, disputa, contestação, aclaración,
# contracargo) or a card block.
_ACTION = re.compile(r"\breclam|\bdisput|\bcontest|\baclaracion|\bcontracargo|\bbloque")
_CARD_BLOCK = re.compile(r"\bbloque")
_HUMAN_OFFER = re.compile(r"\basesor|\bagente\b|\bpersona\b|\batendente\b|\bhumano\b|\bejecutivo\b")
_CLARIFY = re.compile(
    r"\bcual\b|\bqual\b|\bcuales\b|\bquais\b|\bindica|\binform|\bpodrias\b|\bpoderia\b"
    r"|\bmonto\b|\bvalor\b|\bfecha\b|\bdata\b|\bcomercio\b|\bloja\b|\bestabelecimento\b"
)

_ANSWERS: dict[Language, dict[str, str]] = {
    Language.ES: {
        "recognize_yes": "Sí, la reconozco, fui yo.",
        "recognize_no": "No, no la reconozco.",
        "confirm_yes": "Sí, confirmo.",
        "confirm_no": "No, no lo confirmo.",
        "block_yes": "Sí, por favor bloquéala.",
        "block_no": "No, no la bloquees.",
        "human_yes": "Sí, quiero hablar con un asesor.",
        "human_no": "No, gracias, sigamos por aquí.",
        "not_sure": "No estoy seguro.",
    },
    Language.PT: {
        "recognize_yes": "Sim, reconheço, fui eu.",
        "recognize_no": "Não, não reconheço.",
        "confirm_yes": "Sim, confirmo.",
        "confirm_no": "Não, não confirmo.",
        "block_yes": "Sim, pode bloquear o cartão.",
        "block_no": "Não, não precisa bloquear.",
        "human_yes": "Sim, quero falar com um atendente.",
        "human_no": "Não, obrigado, vamos continuar por aqui.",
        "not_sure": "Não tenho certeza.",
    },
}


def classify_question(agent_text: str, clarification_keys: tuple[str, ...] = ()) -> QuestionKind:
    """What the agent asked, from its question sentences only."""
    questions = [s for s in re.split(r"(?<=[.!?\n])\s+", agent_text) if _QUESTION.search(s)]
    norm = normalize(" ".join(questions))
    if not norm:
        return QuestionKind.UNCLASSIFIED
    # A question that asks to confirm a named action is a confirmation even when it mentions the
    # reason: "¿Confirmas crear un reclamo por movimiento no reconocido?" is not asking whether
    # the customer recognizes the charge. "¿Puedes confirmar si reconoces este cargo?" names no
    # action and stays a recognition question.
    if _CONFIRM.search(norm) and _ACTION.search(norm):
        return QuestionKind.CONFIRM
    if _RECOGNIZE.search(norm):
        return QuestionKind.RECOGNIZE
    if _CONFIRM.search(norm):
        return QuestionKind.CONFIRM
    if _CARD_BLOCK.search(norm):
        return QuestionKind.CARD_BLOCK
    if _HUMAN_OFFER.search(norm):
        return QuestionKind.HUMAN_OFFER
    if any(normalize(key) in norm for key in clarification_keys) or _CLARIFY.search(norm):
        return QuestionKind.CLARIFY
    return QuestionKind.UNCLASSIFIED


class ScriptedUser:
    """Plays the customer of one case. ``next_message`` returns None when the user stops."""

    def __init__(self, case: EvalCase) -> None:
        self._case = case
        self._pending = [turn.text for turn in case.turns[1:]]
        # A case may word a yes or a no its own way; its facts still decide which one it is.
        self._answers = _ANSWERS[case.language] | {
            answer.value: text for answer, text in case.facts.answer_wording.items()
        }
        self.events: list[SimEvent] = []

    @property
    def opening(self) -> str:
        return self._case.turns[0].text

    def next_message(self, agent_text: str, turn_index: int) -> str | None:
        """The user's message for ``turn_index`` (>= 1) after the agent said ``agent_text``."""
        if self._pending:
            return self._pending.pop(0)
        if not _QUESTION.search(agent_text):
            return None
        facts = self._case.facts
        kind = classify_question(agent_text, tuple(facts.clarification_answers))
        value: bool | None = None
        if kind == QuestionKind.RECOGNIZE:
            value = bool(facts.recognizes_charge)
            text = self._answers["recognize_yes" if value else "recognize_no"]
        elif kind == QuestionKind.CONFIRM:
            mentions_block = bool(_CARD_BLOCK.search(normalize(agent_text)))
            value = facts.confirms_actions and (facts.wants_card_block or not mentions_block)
            text = self._answers["confirm_yes" if value else "confirm_no"]
        elif kind == QuestionKind.CARD_BLOCK:
            value = facts.wants_card_block
            text = self._answers["block_yes" if value else "block_no"]
        elif kind == QuestionKind.HUMAN_OFFER:
            value = facts.requests_human
            text = self._answers["human_yes" if value else "human_no"]
        elif kind == QuestionKind.CLARIFY:
            text = self._clarification(agent_text)
        else:
            text = facts.clarification_answers.get("default", self._answers["not_sure"])
        self.events.append(SimEvent(turn_index=turn_index, kind=kind, value=value, text=text))
        return text

    def _clarification(self, agent_text: str) -> str:
        answers = self._case.facts.clarification_answers
        norm = normalize(agent_text)
        for key in sorted(answers):
            if key != "default" and normalize(key) in norm:
                return answers[key]
        specific = [answers[key] for key in sorted(answers) if key != "default"]
        if specific:
            return specific[0]
        return answers.get("default", self._answers["not_sure"])
