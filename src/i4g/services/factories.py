"""Factory helpers that instantiate core services based on configuration.

These helpers centralize the logic for honoring the environment-specific
settings declared in :mod:`i4g.settings`. They return the concrete storage or
vector store implementations compatible with the current environment profile,
raising ``NotImplementedError`` when a backend is declared but not yet
implemented.
"""

from __future__ import annotations

import os
from pathlib import Path

from i4g.reports.bundle_builder import BundleBuilder
from i4g.reports.bundle_candidates import BundleCandidateProvider
from i4g.reports.dossier_context import DossierContextLoader
from i4g.services.firestore_writer import FirestoreWriter
from i4g.services.vertex_writer import VertexDocumentWriter
from i4g.settings import get_settings
from i4g.storage import EvidenceStorage
from i4g.store.dossier_queue_store import DossierQueueStore
from i4g.store.entity_store import EntityStore
from i4g.store.ingestion_retry_store import IngestionRetryStore
from i4g.store.ingestion_run_tracker import IngestionRunTracker
from i4g.store.intake_store import IntakeStore
from i4g.store.review_store import ReviewStore
from i4g.store.sql import session_factory as build_sql_session_factory
from i4g.store.sql_writer import SqlWriter
from i4g.store.structured import StructuredStore
from i4g.store.vector import VectorStore


def build_structured_store(db_path: str | Path | None = None) -> StructuredStore:
    """Return a structured-store instance that matches the configured backend.

    Args:
        db_path: Optional path override. When ``None``, the path configured in
            ``settings.storage.sqlite_path`` is used.

    Returns:
        Instantiated :class:`StructuredStore`.

    Raises:
        NotImplementedError: If the configured backend is not yet supported.
    """

    settings = get_settings()
    backend = settings.storage.structured_backend
    if backend == "sqlite":
        return StructuredStore(db_path=db_path)

    if backend == "firestore":
        raise NotImplementedError("Firestore structured backend not implemented yet")

    if backend == "cloudsql":
        raise NotImplementedError("Cloud SQL structured backend not implemented yet")

    raise NotImplementedError(f"Unsupported structured storage backend '{backend}'")


def build_entity_store() -> EntityStore:
    """Instantiate an :class:`EntityStore` backed by the configured SQL engine."""

    session_factory = build_sql_session_factory()
    return EntityStore(session_factory=session_factory)


def build_review_store(db_path: str | Path | None = None) -> ReviewStore:
    """Return a :class:`ReviewStore` honoring the structured backend settings."""

    settings = get_settings()
    backend = settings.storage.structured_backend
    if backend == "sqlite":
        return ReviewStore(db_path=db_path)

    if backend == "firestore":
        raise NotImplementedError("Firestore review backend not implemented yet")

    if backend == "cloudsql":
        raise NotImplementedError("Cloud SQL review backend not implemented yet")

    raise NotImplementedError(f"Unsupported review backend '{backend}'")


def build_vector_store(
    *,
    backend: str | None = None,
    persist_dir: str | Path | None = None,
    embedding_model: str | None = None,
    collection_name: str | None = None,
    reset: bool = False,
) -> VectorStore:
    """Return a vector store implementation consistent with current settings.

    Args:
    backend: Explicit backend to use (overrides configured default).
    persist_dir: Optional directory override for the vector backend.
        embedding_model: Embedding model identifier. Defaults to the configured
            ``settings.vector.embedding_model`` when ``None``.
        collection_name: Optional override for the vector collection/index
            name. Defaults to ``settings.vector.collection``.
        reset: Whether to reset any persisted artifacts before instantiation.

    Returns:
    Configured :class:`VectorStore` instance.

    Raises:
        NotImplementedError: If the configured backend lacks an implementation.
    """

    settings = get_settings()
    resolved_backend = (backend or settings.vector.backend).lower()
    model_name = embedding_model or settings.vector.embedding_model
    collection = collection_name or settings.vector.collection

    if resolved_backend in {"chroma", "faiss"}:
        return VectorStore(
            persist_dir=str(persist_dir) if persist_dir is not None else None,
            embedding_model=model_name,
            backend=resolved_backend,
            collection_name=collection,
            reset=reset,
        )

    if resolved_backend == "pgvector":
        raise NotImplementedError("pgvector backend not implemented yet")

    if resolved_backend == "vertex_ai":
        raise NotImplementedError("Vertex AI vector backend not implemented yet")

    raise NotImplementedError(f"Unsupported vector backend '{resolved_backend}'")


def build_intake_store(db_path: str | Path | None = None) -> IntakeStore:
    """Return an :class:`IntakeStore` aligned with the structured backend."""

    settings = get_settings()
    backend = settings.storage.structured_backend
    if backend == "sqlite":
        return IntakeStore(db_path=db_path)

    if backend == "firestore":
        raise NotImplementedError("Firestore intake backend not implemented yet")

    if backend == "cloudsql":
        raise NotImplementedError("Cloud SQL intake backend not implemented yet")

    raise NotImplementedError(f"Unsupported intake storage backend '{backend}'")


def build_evidence_storage(*, local_dir: str | Path | None = None) -> EvidenceStorage:
    """Instantiate the configured evidence storage provider."""

    path = Path(local_dir) if isinstance(local_dir, str) else local_dir
    return EvidenceStorage(local_dir=path)


def build_sql_writer(*, settings: "Settings" | None = None) -> SqlWriter:
    """Create a SqlWriter bound to the configured SQLAlchemy engine."""

    session_factory = build_sql_session_factory(settings=settings)
    return SqlWriter(session_factory=session_factory)


def build_ingestion_run_tracker(*, settings: "Settings" | None = None) -> IngestionRunTracker:
    """Return a tracker for ingestion run metrics."""

    session_factory = build_sql_session_factory(settings=settings)
    return IngestionRunTracker(session_factory=session_factory)


def build_ingestion_retry_store(*, settings: "Settings" | None = None) -> IngestionRetryStore:
    """Return a store for managing ingestion retry queue entries."""

    session_factory = build_sql_session_factory(settings=settings)
    return IngestionRetryStore(session_factory=session_factory)


def build_vertex_writer(*, settings: "Settings" | None = None) -> VertexDocumentWriter:
    """Instantiate a Vertex document writer honoring current settings/env."""

    resolved = settings or get_settings()
    project = os.getenv("I4G_VERTEX_SEARCH_PROJECT") or resolved.vector.vertex_ai_project
    location = os.getenv("I4G_VERTEX_SEARCH_LOCATION") or resolved.vector.vertex_ai_location or "global"
    data_store = os.getenv("I4G_VERTEX_SEARCH_DATA_STORE") or resolved.vector.vertex_ai_data_store
    branch = os.getenv("I4G_VERTEX_SEARCH_BRANCH") or resolved.vector.vertex_ai_branch or "default_branch"

    if not project or not data_store:
        raise RuntimeError(
            "Vertex writer requires project and data store. Set I4G_VERTEX_SEARCH_* env vars or vector settings.",
        )

    return VertexDocumentWriter(
        project=project,
        location=location,
        data_store_id=data_store,
        branch=branch,
        default_dataset=resolved.ingestion.default_dataset,
        timeout_seconds=resolved.ingestion.fanout_timeout_seconds,
    )


def build_firestore_writer(*, settings: "Settings" | None = None) -> FirestoreWriter:
    """Instantiate a Firestore writer aligned with storage settings."""

    resolved = settings or get_settings()
    project = resolved.storage.firestore_project
    collection = resolved.storage.firestore_collection

    if not project:
        raise RuntimeError(
            "Firestore writer requires storage.firestore_project; set I4G_STORAGE__FIRESTORE__PROJECT.",
        )

    return FirestoreWriter(project=project, collection=collection)


def build_dossier_queue_store(db_path: str | Path | None = None) -> DossierQueueStore:
    """Return a DossierQueueStore instance backed by the configured SQLite DB."""

    return DossierQueueStore(db_path=db_path)


def build_bundle_builder(
    *,
    queue_store: DossierQueueStore | None = None,
    shared_drive_parent_id: str | None = None,
) -> BundleBuilder:
    """Instantiate a BundleBuilder with Drive metadata derived from settings."""

    settings = get_settings()
    parent_id = shared_drive_parent_id or settings.report.drive_parent_id
    store = queue_store or build_dossier_queue_store()
    return BundleBuilder(queue_store=store, shared_drive_parent_id=parent_id)


def build_bundle_candidate_provider(
    *,
    review_store: ReviewStore | None = None,
    structured_store: StructuredStore | None = None,
) -> BundleCandidateProvider:
    """Return a provider that yields dossier candidates from accepted reviews."""

    resolved_review = review_store or build_review_store()
    resolved_structured = structured_store or build_structured_store()
    return BundleCandidateProvider(review_store=resolved_review, structured_store=resolved_structured)


def build_dossier_context_loader(
    *,
    structured_store: StructuredStore | None = None,
    review_store: ReviewStore | None = None,
) -> DossierContextLoader:
    """Instantiate a context loader that hydrates dossier cases with metadata."""

    resolved_structured = structured_store or build_structured_store()
    resolved_review = review_store or build_review_store()
    return DossierContextLoader(structured_store=resolved_structured, review_store=resolved_review)


__all__ = [
    "build_structured_store",
    "build_entity_store",
    "build_review_store",
    "build_vector_store",
    "build_intake_store",
    "build_evidence_storage",
    "build_sql_writer",
    "build_ingestion_run_tracker",
    "build_ingestion_retry_store",
    "build_vertex_writer",
    "build_firestore_writer",
    "build_dossier_queue_store",
    "build_bundle_builder",
    "build_bundle_candidate_provider",
    "build_dossier_context_loader",
]
