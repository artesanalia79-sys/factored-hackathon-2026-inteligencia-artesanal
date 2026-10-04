"""Labeled first messages for the learned intent router (Task 18).

Two versioned, synthetic sources:

- the corpus, ``eval/router/corpus/<intent>.yaml``: messages written for this task, one file per
  intent, grouped by scenario (one scenario in five dialects) so that paraphrases of a scenario
  never land on both sides of a split;
- the external check, ``eval/router/external_check.yaml``: messages other teammates wrote for
  other purposes (Task 10, the keyword tests, the dev cases), never used to fit, calibrate or
  tune the router.

Nothing here reads the sealed held-out set.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import Field, StringConstraints, ValidationError

from bankagent.contracts.base import Contract
from bankagent.contracts.enums import Dialect, Intent, Language, Provenance
from bankagent.interpret.keywords import normalize

ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = ROOT / "eval" / "router" / "corpus"
EXTERNAL_CHECK = ROOT / "eval" / "router" / "external_check.yaml"
EXTERNAL_GROUP = "external"

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
GroupId = Annotated[str, StringConstraints(pattern=r"^[a-z]{3}-\d{2}$")]


class CorpusError(ValueError):
    """A corpus or external-check file breaks one of the rules below."""


class Utterance(Contract):
    dialect: Dialect
    text: Text


class ScenarioGroup(Contract):
    id: GroupId
    utterances: tuple[Utterance, ...] = Field(min_length=1)


class CorpusFile(Contract):
    """One intent's file: every message in it carries that intent."""

    intent: Intent
    provenance: Provenance
    author: str = Field(min_length=1)
    reviewed_by: str | None = None
    groups: tuple[ScenarioGroup, ...] = Field(min_length=1)


class ExternalItem(Contract):
    text: Text
    intent: Intent
    dialect: Dialect
    sources: tuple[str, ...] = Field(min_length=1)
    label_by: str = Field(min_length=1)


class ExternalCheckFile(Contract):
    items: tuple[ExternalItem, ...] = Field(min_length=1)


def language_of(dialect: Dialect) -> Language:
    return Language.PT if dialect.value.startswith("pt") else Language.ES


@dataclass(frozen=True, slots=True)
class Example:
    """One labeled message; ``group`` is its scenario id (``"external"`` in the external check)."""

    text: str
    intent: Intent
    dialect: Dialect
    group: str

    @property
    def language(self) -> Language:
        return language_of(self.dialect)


@dataclass(frozen=True, slots=True)
class Corpus:
    examples: tuple[Example, ...]
    sha256: str
    reviewed_by: Mapping[Intent, str | None]


@dataclass(frozen=True, slots=True)
class ExternalCheck:
    examples: tuple[Example, ...]
    sha256: str


def digest(paths: Iterable[Path], root: Path = ROOT) -> str:
    """sha256 over each file's path (relative to ``root`` when inside it) and its LF content."""
    sha = hashlib.sha256()
    for path in sorted(paths):
        name = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        sha.update(name.encode("utf-8") + b"\0")
        sha.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return sha.hexdigest()


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CorpusError(f"{path.name}: not valid YAML ({exc})") from exc


def _unique_texts(examples: Iterable[Example], where: str) -> None:
    seen: dict[str, str] = {}
    for example in examples:
        key = normalize(example.text)
        if key in seen:
            raise CorpusError(
                f"{where}: '{example.text}' ({example.group}) repeats a message of {seen[key]}"
            )
        seen[key] = example.group


def load_corpus(directory: Path = CORPUS_DIR) -> Corpus:
    """Every ``<intent>.yaml`` in ``directory``: one file per intent, unique groups and messages."""
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise CorpusError(f"no corpus files in {directory}")
    examples: list[Example] = []
    reviewed_by: dict[Intent, str | None] = {}
    group_ids: set[str] = set()
    for path in paths:
        try:
            parsed = CorpusFile.model_validate(_read_yaml(path))
        except ValidationError as exc:
            raise CorpusError(f"{path.name}: {exc}") from exc
        if path.stem != parsed.intent.value:
            raise CorpusError(f"{path.name}: holds intent '{parsed.intent.value}'")
        reviewed_by[parsed.intent] = parsed.reviewed_by
        for group in parsed.groups:
            if group.id in group_ids:
                raise CorpusError(f"{path.name}: group id {group.id} is used twice")
            group_ids.add(group.id)
            examples.extend(
                Example(u.text, parsed.intent, u.dialect, group.id) for u in group.utterances
            )
    missing = sorted(intent.value for intent in Intent if intent not in reviewed_by)
    if missing:
        raise CorpusError(f"no corpus file for: {', '.join(missing)}")
    _unique_texts(examples, directory.name)
    return Corpus(tuple(examples), digest(paths), reviewed_by)


def load_external(path: Path = EXTERNAL_CHECK) -> ExternalCheck:
    try:
        parsed = ExternalCheckFile.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise CorpusError(f"{path.name}: {exc}") from exc
    examples = tuple(
        Example(item.text, item.intent, item.dialect, EXTERNAL_GROUP) for item in parsed.items
    )
    _unique_texts(examples, path.name)
    return ExternalCheck(examples, digest([path]))


def shared_messages(corpus: Corpus, external: ExternalCheck) -> tuple[str, ...]:
    """Messages present in both sources (after normalization): the check must stay external."""
    corpus_texts = {normalize(e.text) for e in corpus.examples}
    return tuple(e.text for e in external.examples if normalize(e.text) in corpus_texts)
