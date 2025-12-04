"""Tests for the Firestore writer used by the ingestion fan-out."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from i4g.services.firestore_writer import FirestoreWriter, FirestoreWriterError
from i4g.store.sql_writer import (
    CaseBundle,
    CasePayload,
    EntityMentionPayload,
    EntityPayload,
    SourceDocumentPayload,
    SqlWriterResult,
)


class _FakeDocumentReference:
    def __init__(self, path: str) -> None:
        self.path = path

    def collection(self, name: str) -> "_FakeCollectionReference":
        return _FakeCollectionReference(f"{self.path}/{name}")


class _FakeCollectionReference:
    def __init__(self, path: str) -> None:
        self._path = path

    def document(self, doc_id: str) -> _FakeDocumentReference:
        return _FakeDocumentReference(f"{self._path}/{doc_id}")


class _FakeBatch:
    def __init__(self, client: "_FakeFirestoreClient") -> None:
        self._client = client
        self._operations: List[Tuple[str, Dict[str, Any]]] = []

    def set(self, doc_ref: _FakeDocumentReference, payload: Dict[str, Any]) -> None:
        self._operations.append((doc_ref.path, payload))

    def commit(self) -> None:
        if self._client.raise_on_commit:
            raise RuntimeError("commit-failed")
        self._client.commits.append(list(self._operations))
        self._operations.clear()


class _FakeFirestoreClient:
    def __init__(self, *, raise_on_commit: bool = False) -> None:
        self.raise_on_commit = raise_on_commit
        self.commits: List[List[Tuple[str, Dict[str, Any]]]] = []

    def collection(self, name: str) -> _FakeCollectionReference:
        return _FakeCollectionReference(name)

    def batch(self) -> _FakeBatch:
        return _FakeBatch(self)


def _flatten(commits: List[List[Tuple[str, Dict[str, Any]]]]) -> List[Tuple[str, Dict[str, Any]]]:
    flattened: List[Tuple[str, Dict[str, Any]]] = []
    for batch in commits:
        flattened.extend(batch)
    return flattened


def test_firestore_writer_persists_case_documents_and_entities():
    client = _FakeFirestoreClient()
    writer = FirestoreWriter(project="demo", collection="cases", client=client, batch_size=2)

    case_payload = CasePayload(
        dataset="demo",
        source_type="ingest",
        classification="investment",
        confidence=0.87,
        text="wallet verification",
        metadata={
            "loss_amount_usd": 125000,
            "jurisdiction": "US-CA",
            "victim_country": "US",
            "scammer_country": "CN",
        },
    )
    doc_payload = SourceDocumentPayload(
        alias="primary", title="alert", text="wallet verification", metadata={"foo": "bar"}
    )
    entity_payload = EntityPayload(
        alias="organizations-0",
        entity_type="organizations",
        canonical_value="TrustWallet",
        confidence=0.9,
    )
    entity_payload.mentions.append(
        EntityMentionPayload(document_alias="primary", span_start=0, span_end=6, sentence="TrustWallet message")
    )

    bundle = CaseBundle(case=case_payload, documents=[doc_payload], entities=[entity_payload])
    sql_result = SqlWriterResult(case_id="case-1", document_ids=["doc-1"], entity_ids=["ent-1"], indicator_ids=[])

    result = writer.persist_case_bundle(bundle, sql_result, ingestion_run_id="run-1")

    assert result.case_path == "cases/case-1"
    assert result.document_paths == ["cases/case-1/documents/doc-1"]
    assert result.entity_paths == ["cases/case-1/entities/ent-1"]

    ops = dict(_flatten(client.commits))
    case_doc = ops["cases/case-1"]
    assert case_doc["ingestion_run_id"] == "run-1"
    assert case_doc["document_ids"] == ["doc-1"]
    assert case_doc["bundle_metrics"]["loss_band"] == "100k-250k"
    assert case_doc["bundle_metrics"]["cross_border"] is True

    entity_doc = ops["cases/case-1/entities/ent-1"]
    assert entity_doc["mentions"][0]["document_id"] == "doc-1"


def test_firestore_writer_wraps_commit_errors():
    client = _FakeFirestoreClient(raise_on_commit=True)
    writer = FirestoreWriter(project="demo", collection="cases", client=client)

    case_payload = CasePayload(
        dataset="demo",
        source_type="ingest",
        classification="investment",
        confidence=0.5,
        text="wallet",
    )
    bundle = CaseBundle(case=case_payload, documents=[], entities=[])
    sql_result = SqlWriterResult(case_id="case-2", document_ids=[], entity_ids=[], indicator_ids=[])

    with pytest.raises(FirestoreWriterError):
        writer.persist_case_bundle(bundle, sql_result)
