"""Unit tests for the SQL-backed EntityStore helper."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from i4g.store import sql as sql_schema
from i4g.store.entity_store import EntityStore


def _session_factory() -> sessionmaker:
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    sql_schema.METADATA.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed_case_with_entity(
    session,
    *,
    case_id: str,
    dataset: str,
    entity_value: str,
    loss_amount: float | None = None,
    entity_type: str = "bank_account",
    last_seen_offset_days: int = 0,
) -> None:
    case_metadata: Dict[str, Any] = {"dataset": dataset}
    if loss_amount is not None:
        case_metadata["loss_amount"] = loss_amount

    now = datetime.utcnow()
    last_seen_at = now - timedelta(days=last_seen_offset_days)

    session.execute(
        sql_schema.cases.insert(),
        {
            "case_id": case_id,
            "dataset": dataset,
            "source_type": "ocr",
            "classification": "romance",
            "confidence": 0.9,
            "detected_at": datetime.utcnow(),
            "reported_at": datetime.utcnow(),
            "raw_text_sha256": f"sha-{case_id}",
            "status": "open",
            "metadata": case_metadata,
        },
    )
    session.execute(
        sql_schema.entities.insert(),
        {
            "entity_id": f"entity-{case_id}",
            "case_id": case_id,
            "entity_type": entity_type,
            "canonical_value": entity_value,
            "metadata": {"dataset": dataset},
            "last_seen_at": last_seen_at,
        },
    )
    session.commit()


def test_search_cases_by_indicator_filters_dataset_and_loss_bucket():
    factory = _session_factory()
    store = EntityStore(session_factory=factory)
    with factory() as session:
        _seed_case_with_entity(
            session,
            case_id="case-a",
            dataset="retrieval_poc_dev",
            entity_value="021000021-123456789",
            loss_amount=25000,
        )

    results = store.search_cases_by_indicator(
        indicator_type="bank_account",
        value="021000021",
        match_mode="prefix",
        datasets=["retrieval_poc_dev"],
        loss_buckets=["10k-50k"],
        limit=10,
    )

    assert len(results) == 1
    assert results[0]["case_id"] == "case-a"
    assert results[0]["loss_amount"] == 25000


def test_search_cases_by_indicator_honors_mismatch_filters():
    factory = _session_factory()
    store = EntityStore(session_factory=factory)
    with factory() as session:
        _seed_case_with_entity(
            session,
            case_id="case-b",
            dataset="account_list",
            entity_value="999-222-111",
            loss_amount=5000,
        )

    # Dataset mismatch
    assert (
        store.search_cases_by_indicator(
            indicator_type="bank_account",
            value="999",
            match_mode="prefix",
            datasets=["retrieval_poc_dev"],
            loss_buckets=None,
            limit=5,
        )
        == []
    )

    # Loss bucket mismatch
    assert (
        store.search_cases_by_indicator(
            indicator_type="bank_account",
            value="999",
            match_mode="contains",
            datasets=["account_list"],
            loss_buckets=[">50k"],
            limit=5,
        )
        == []
    )


def test_list_datasets_orders_by_frequency():
    factory = _session_factory()
    store = EntityStore(session_factory=factory)
    with factory() as session:
        _seed_case_with_entity(session, case_id="case-c", dataset="network_smoke", entity_value="ua-1")
        _seed_case_with_entity(session, case_id="case-d", dataset="network_smoke", entity_value="ua-2")
        _seed_case_with_entity(session, case_id="case-e", dataset="retrieval_poc_dev", entity_value="ua-3")

    datasets = store.list_datasets()

    assert datasets[0] == "network_smoke"  # appears most frequently
    assert set(datasets) == {"network_smoke", "retrieval_poc_dev"}


def test_list_datasets_gracefully_handles_missing_tables():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    store = EntityStore(session_factory=factory)

    assert store.list_datasets() == []


def test_list_entity_examples_returns_unique_values():
    factory = _session_factory()
    store = EntityStore(session_factory=factory)
    with factory() as session:
        _seed_case_with_entity(
            session,
            case_id="case-f",
            dataset="network_smoke",
            entity_value="Mozilla/5.0",
            entity_type="browser_agent",
            last_seen_offset_days=1,
        )
        _seed_case_with_entity(
            session,
            case_id="case-g",
            dataset="network_smoke",
            entity_value="Mozilla/5.0",
            entity_type="browser_agent",
            last_seen_offset_days=2,
        )
        _seed_case_with_entity(
            session,
            case_id="case-h",
            dataset="network_smoke",
            entity_value="Safari/17",
            entity_type="browser_agent",
        )

    examples = store.list_entity_examples(entity_types=["browser_agent"], per_type_limit=2)

    assert "browser_agent" in examples
    values = [entry["value"] for entry in examples["browser_agent"]]
    assert values == ["Safari/17", "Mozilla/5.0"]


def test_list_entity_examples_filters_by_dataset():
    factory = _session_factory()
    store = EntityStore(session_factory=factory)
    with factory() as session:
        _seed_case_with_entity(
            session,
            case_id="case-i",
            dataset="network_smoke",
            entity_value="1.1.1.1",
            entity_type="ip_address",
        )
        _seed_case_with_entity(
            session,
            case_id="case-j",
            dataset="retrieval_poc_dev",
            entity_value="2.2.2.2",
            entity_type="ip_address",
        )

    filtered = store.list_entity_examples(entity_types=["ip_address"], datasets=["network_smoke"], per_type_limit=5)

    values = [entry["value"] for entry in filtered["ip_address"]]
    assert values == ["1.1.1.1"]


def test_list_entity_examples_handles_missing_tables():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    store = EntityStore(session_factory=factory)

    assert store.list_entity_examples(entity_types=["bank_account"], per_type_limit=3) == {}
