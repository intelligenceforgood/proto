"""Firestore writer used by the ingestion pipeline fan-out."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore

from i4g.reports.bundle_metrics import compute_bundle_metrics
from i4g.store.sql_writer import (
    CaseBundle,
    CasePayload,
    EntityMentionPayload,
    EntityPayload,
    IndicatorPayload,
    IndicatorSourcePayload,
    SourceDocumentPayload,
    SqlWriterResult,
)

LOGGER = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialise_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _strip_none(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


@dataclass(slots=True)
class FirestoreWriteResult:
    """Summary of the Firestore documents written for a case."""

    case_path: str
    document_paths: List[str] = field(default_factory=list)
    entity_paths: List[str] = field(default_factory=list)
    indicator_paths: List[str] = field(default_factory=list)


class FirestoreWriterError(RuntimeError):
    """Raised when Firestore writes fail."""


class FirestoreWriter:
    """Persist case bundles into Firestore collections using batch writes."""

    def __init__(
        self,
        *,
        project: str,
        collection: str,
        batch_size: int = 400,
        client: Optional[firestore.Client] = None,
    ) -> None:
        if not project:
            raise ValueError("FirestoreWriter requires a project ID")
        if not collection:
            raise ValueError("FirestoreWriter requires a collection name")

        self._client = client or firestore.Client(project=project)
        self._collection = self._client.collection(collection)
        # Firestore batches are capped at 500 operations.
        self._batch_size = max(1, min(batch_size, 500))

    def persist_case_bundle(
        self,
        bundle: CaseBundle,
        sql_result: SqlWriterResult,
        *,
        ingestion_run_id: str | None = None,
    ) -> FirestoreWriteResult:
        """Persist a case bundle and its child artifacts into Firestore.

        Args:
            bundle: Complete case payload mirrored from the SQL writer.
            sql_result: Result returned by the SQL writer containing the canonical IDs.
            ingestion_run_id: Optional run identifier stored alongside the case document.

        Returns:
            :class:`FirestoreWriteResult` describing the written document paths.
        """

        if not sql_result:
            raise FirestoreWriterError("SQL writer result is required for Firestore fan-out")

        case_id = sql_result.case_id
        case_ref = self._collection.document(case_id)
        timestamp = _utcnow()

        batch = self._client.batch()
        operations = 0

        def _commit_batch(force: bool = False) -> None:
            nonlocal batch, operations
            if operations == 0 and not force:
                return
            batch.commit()
            batch = self._client.batch()
            operations = 0

        def _queue_set(doc_ref: firestore.DocumentReference, payload: Dict[str, Any]) -> None:
            nonlocal operations
            batch.set(doc_ref, payload)
            operations += 1
            if operations >= self._batch_size:
                _commit_batch()

        document_alias_map: Dict[str, str] = {}
        entity_alias_map: Dict[str, str] = {}

        result = FirestoreWriteResult(case_path=case_ref.path)

        try:
            case_payload = self._build_case_payload(bundle.case, sql_result, ingestion_run_id, timestamp)
            _queue_set(case_ref, case_payload)

            for doc_payload, document_id in zip(bundle.documents, sql_result.document_ids):
                if doc_payload.alias:
                    document_alias_map[doc_payload.alias] = document_id
                doc_ref = case_ref.collection("documents").document(document_id)
                serialised = self._build_document_payload(doc_payload, document_id, timestamp)
                _queue_set(doc_ref, serialised)
                result.document_paths.append(doc_ref.path)

            for entity_payload, entity_id in zip(bundle.entities, sql_result.entity_ids):
                if entity_payload.alias:
                    entity_alias_map[entity_payload.alias] = entity_id
                entity_ref = case_ref.collection("entities").document(entity_id)
                serialised_entity = self._build_entity_payload(
                    entity_payload,
                    entity_id,
                    document_alias_map,
                    timestamp,
                )
                _queue_set(entity_ref, serialised_entity)
                result.entity_paths.append(entity_ref.path)

            for indicator_payload, indicator_id in zip(bundle.indicators, sql_result.indicator_ids):
                indicator_ref = case_ref.collection("indicators").document(indicator_id)
                serialised_indicator = self._build_indicator_payload(
                    indicator_payload,
                    indicator_id,
                    document_alias_map,
                    entity_alias_map,
                    bundle.case.dataset,
                    timestamp,
                )
                _queue_set(indicator_ref, serialised_indicator)
                result.indicator_paths.append(indicator_ref.path)

            _commit_batch(force=True)
        except Exception as exc:  # pragma: no cover - surfaced via unit tests
            LOGGER.exception("Firestore write failed for case_id=%s", case_id)
            raise FirestoreWriterError(f"Firestore write failed for case_id={case_id}: {exc}") from exc

        return result

    def _build_case_payload(
        self,
        payload: CasePayload,
        sql_result: SqlWriterResult,
        ingestion_run_id: str | None,
        timestamp: datetime,
    ) -> Dict[str, Any]:
        data = {
            "case_id": sql_result.case_id,
            "dataset": payload.dataset,
            "source_type": payload.source_type,
            "classification": payload.classification,
            "confidence": float(payload.confidence),
            "status": payload.status,
            "metadata": payload.metadata,
            "text": payload.text,
            "raw_text_sha256": payload.raw_text_sha256,
            "detected_at": _serialise_timestamp(payload.detected_at),
            "reported_at": _serialise_timestamp(payload.reported_at),
            "deleted_at": _serialise_timestamp(payload.deleted_at),
            "is_deleted": payload.is_deleted,
            "ingestion_run_id": ingestion_run_id,
            "document_ids": sql_result.document_ids,
            "entity_ids": sql_result.entity_ids,
            "indicator_ids": sql_result.indicator_ids,
            "updated_at": timestamp,
            "created_at": timestamp,
        }
        metrics = compute_bundle_metrics(payload.metadata)
        data["bundle_metrics"] = metrics.to_dict()
        return _strip_none(data)

    def _build_document_payload(
        self,
        payload: SourceDocumentPayload,
        document_id: str,
        timestamp: datetime,
    ) -> Dict[str, Any]:
        data = {
            "document_id": document_id,
            "alias": payload.alias,
            "title": payload.title,
            "source_url": payload.source_url,
            "mime_type": payload.mime_type,
            "text": payload.text,
            "text_sha256": payload.text_sha256,
            "excerpt": payload.excerpt,
            "chunk_index": payload.chunk_index,
            "chunk_count": payload.chunk_count,
            "score": float(payload.score) if payload.score is not None else None,
            "captured_at": _serialise_timestamp(payload.captured_at),
            "metadata": payload.metadata,
            "updated_at": timestamp,
            "created_at": timestamp,
        }
        return _strip_none(data)

    def _build_entity_payload(
        self,
        payload: EntityPayload,
        entity_id: str,
        document_alias_map: Dict[str, str],
        timestamp: datetime,
    ) -> Dict[str, Any]:
        mentions: List[Dict[str, Any]] = []
        for mention in payload.mentions:
            serialised = self._build_entity_mention(mention, document_alias_map)
            if serialised:
                mentions.append(serialised)

        data = {
            "entity_id": entity_id,
            "alias": payload.alias,
            "entity_type": payload.entity_type,
            "canonical_value": payload.canonical_value,
            "raw_value": payload.raw_value,
            "confidence": float(payload.confidence),
            "first_seen_at": _serialise_timestamp(payload.first_seen_at),
            "last_seen_at": _serialise_timestamp(payload.last_seen_at),
            "metadata": payload.metadata,
            "mentions": mentions,
            "updated_at": timestamp,
            "created_at": timestamp,
        }
        return _strip_none(data)

    def _build_indicator_payload(
        self,
        payload: IndicatorPayload,
        indicator_id: str,
        document_alias_map: Dict[str, str],
        entity_alias_map: Dict[str, str],
        case_dataset: Optional[str],
        timestamp: datetime,
    ) -> Dict[str, Any]:
        sources: List[Dict[str, Any]] = []
        for source in payload.sources:
            serialised = self._build_indicator_source(source, document_alias_map, entity_alias_map)
            if serialised:
                sources.append(serialised)

        data = {
            "indicator_id": indicator_id,
            "category": payload.category,
            "type": payload.type,
            "number": payload.number,
            "dataset": payload.dataset or case_dataset,
            "item": payload.item,
            "status": payload.status,
            "confidence": float(payload.confidence),
            "first_seen_at": _serialise_timestamp(payload.first_seen_at),
            "last_seen_at": _serialise_timestamp(payload.last_seen_at),
            "metadata": payload.metadata,
            "sources": sources,
            "updated_at": timestamp,
            "created_at": timestamp,
        }
        return _strip_none(data)

    def _build_entity_mention(
        self,
        mention: EntityMentionPayload,
        document_alias_map: Dict[str, str],
    ) -> Dict[str, Any] | None:
        document_id = self._resolve_document_id(mention.document_id, mention.document_alias, document_alias_map)
        data = {
            "document_id": document_id,
            "document_alias": mention.document_alias,
            "span_start": mention.span_start,
            "span_end": mention.span_end,
            "sentence": mention.sentence,
            "metadata": mention.metadata,
        }
        serialised = _strip_none(data)
        if not serialised.get("document_id") and not serialised.get("sentence"):
            return None
        return serialised

    def _build_indicator_source(
        self,
        source: IndicatorSourcePayload,
        document_alias_map: Dict[str, str],
        entity_alias_map: Dict[str, str],
    ) -> Dict[str, Any] | None:
        document_id = self._resolve_document_id(source.document_id, source.document_alias, document_alias_map)
        entity_id = self._resolve_entity_id(source.entity_id, source.entity_alias, entity_alias_map)
        data = {
            "document_id": document_id,
            "document_alias": source.document_alias,
            "entity_id": entity_id,
            "entity_alias": source.entity_alias,
            "evidence_score": source.evidence_score,
            "explanation": source.explanation,
            "metadata": source.metadata,
        }
        serialised = _strip_none(data)
        if not serialised:
            return None
        return serialised

    @staticmethod
    def _resolve_document_id(
        explicit_id: str | None,
        alias: str | None,
        alias_map: Dict[str, str],
    ) -> str | None:
        if explicit_id:
            return explicit_id
        if alias and alias in alias_map:
            return alias_map[alias]
        return None

    @staticmethod
    def _resolve_entity_id(
        explicit_id: str | None,
        alias: str | None,
        alias_map: Dict[str, str],
    ) -> str | None:
        if explicit_id:
            return explicit_id
        if alias and alias in alias_map:
            return alias_map[alias]
        return None


__all__ = ["FirestoreWriter", "FirestoreWriterError", "FirestoreWriteResult"]
