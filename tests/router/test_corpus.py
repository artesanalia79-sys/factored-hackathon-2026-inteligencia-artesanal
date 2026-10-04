"""The router's synthetic corpus and its external check (Task 18)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml

from bankagent.contracts.enums import Dialect, Intent
from bankagent.eval.detectors import detect_pii
from bankagent.fixtures.builder import load_source
from bankagent.router.corpus import (
    ROOT,
    CorpusError,
    digest,
    load_corpus,
    load_external,
    shared_messages,
)

QUOTA_DIALECTS = (Dialect.ES_MX, Dialect.ES_CO, Dialect.ES_AR, Dialect.PT_BR)


def test_corpus_covers_every_intent_and_the_dialect_quotas() -> None:
    corpus = load_corpus()
    examples = corpus.examples
    groups_per_intent = Counter(intent for intent, _ in {(e.intent, e.group) for e in examples})
    assert set(groups_per_intent) == set(Intent)
    assert min(groups_per_intent.values()) >= 15
    dialects = Counter(e.dialect for e in examples)
    for dialect in QUOTA_DIALECTS:
        assert dialects[dialect] / len(examples) >= 0.15, dialect
    sizes = Counter(e.group for e in examples)
    assert min(sizes.values()) >= 3
    labels_per_group = {g: {e.intent for e in examples if e.group == g} for g in sizes}
    assert all(len(labels) == 1 for labels in labels_per_group.values())


def test_no_message_holds_personal_data_or_names_a_fixture_customer() -> None:
    customers = load_source().customers
    names = [c.first_name for c in customers]
    documents = [c.document_number for c in customers]
    for example in (*load_corpus().examples, *load_external().examples):
        found = detect_pii(example.text, forbidden_names=names, forbidden_values=documents)
        if example.group == "external":
            # Copied verbatim from the keyword tests, which name a fixture customer on purpose.
            found = [kind for kind in found if kind != "other_customer_name"]
        assert found == [], (example.group, example.text, found)


def test_the_external_check_never_repeats_a_corpus_message() -> None:
    assert shared_messages(load_corpus(), load_external()) == ()


def test_the_external_check_quotes_its_sources_verbatim() -> None:
    raw = yaml.safe_load((ROOT / "eval" / "router" / "external_check.yaml").read_text("utf-8"))
    for item in raw["items"]:
        assert item["label_by"] in {"source", "claude-code"}
        for source in item["sources"]:
            assert item["text"] in (ROOT / source).read_text(encoding="utf-8"), (source, item)


# ---------------------------------------------------------------------------
# Loader rules, on a small corpus written to a temporary directory
# ---------------------------------------------------------------------------
def _write_corpus(directory: Path, texts: dict[Intent, list[str]] | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for n, intent in enumerate(Intent, 1):
        messages = (texts or {}).get(intent, [f"mensaje de prueba {n} para {intent.value}"])
        data = {
            "intent": intent.value,
            "provenance": "llm_generated_reviewed",
            "author": "test",
            "reviewed_by": None,
            "groups": [
                {
                    "id": f"{intent.value[:3]}-{n:02d}",
                    "utterances": [{"dialect": "es-MX", "text": t} for t in messages],
                }
            ],
        }
        (directory / f"{intent.value}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_a_valid_small_corpus_loads(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    corpus = load_corpus(tmp_path)
    assert len(corpus.examples) == len(Intent)
    assert set(corpus.reviewed_by.values()) == {None}


def test_a_file_must_hold_the_intent_it_is_named_after(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    (tmp_path / "attack.yaml").rename(tmp_path / "zzz.yaml")
    with pytest.raises(CorpusError, match=r"zzz\.yaml: holds intent 'attack'"):
        load_corpus(tmp_path)


def test_every_intent_needs_a_file(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    (tmp_path / "attack.yaml").unlink()
    with pytest.raises(CorpusError, match="no corpus file for: attack"):
        load_corpus(tmp_path)


def test_a_message_may_appear_only_once_even_with_other_accents(tmp_path: Path) -> None:
    _write_corpus(
        tmp_path,
        {
            Intent.CARD_BLOCK: ["Perdí mi tarjeta"],
            Intent.DISPUTE_STATUS: ["perdi mi tarjeta"],
        },
    )
    with pytest.raises(CorpusError, match="repeats a message"):
        load_corpus(tmp_path)


def test_group_ids_are_unique(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    path = tmp_path / "card_block.yaml"
    data = yaml.safe_load(path.read_text("utf-8"))
    data["groups"][0]["id"] = "att-08"  # the attack file's group id
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(CorpusError, match="att-08 is used twice"):
        load_corpus(tmp_path)


@pytest.mark.parametrize(
    "broken",
    ["intent: [unclosed", "intent: card_block\ngroups: []", "dialect: xx"],
)
def test_malformed_files_are_refused(tmp_path: Path, broken: str) -> None:
    _write_corpus(tmp_path)
    (tmp_path / "card_block.yaml").write_text(broken, encoding="utf-8")
    with pytest.raises(CorpusError, match=r"card_block\.yaml"):
        load_corpus(tmp_path)


def test_an_empty_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="no corpus files"):
        load_corpus(tmp_path)


def test_digest_follows_content_and_ignores_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    path.write_bytes(b"a: 1\nb: 2\n")
    lf = digest([path], root=tmp_path)
    path.write_bytes(b"a: 1\r\nb: 2\r\n")
    assert digest([path], root=tmp_path) == lf
    path.write_bytes(b"a: 1\nb: 3\n")
    assert digest([path], root=tmp_path) != lf
