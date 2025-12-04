"""Unit tests for the dossier bundle builder scaffolding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List

from i4g.reports.bundle_builder import BundleBuilder, BundleCriteria, DossierCandidate
from i4g.reports.bundle_candidates import BundleCandidateProvider
from i4g.store.dossier_queue_store import DossierQueueStore


def test_generate_plans_filters_by_loss_and_recency() -> None:
    builder = BundleBuilder(queue_store=_MemoryQueueStore())
    now = datetime(2025, 12, 3, tzinfo=timezone.utc)
    candidates = [
        DossierCandidate(
            case_id="case-high",
            loss_amount_usd=Decimal("100000"),
            accepted_at=now - timedelta(days=5),
            jurisdiction="US-CA",
        ),
        DossierCandidate(
            case_id="case-low",
            loss_amount_usd=Decimal("10000"),
            accepted_at=now - timedelta(days=5),
            jurisdiction="US-CA",
        ),
        DossierCandidate(
            case_id="case-stale",
            loss_amount_usd=Decimal("75000"),
            accepted_at=now - timedelta(days=60),
            jurisdiction="US-CA",
        ),
    ]
    criteria = BundleCriteria(
        min_loss_usd=Decimal("50000"),
        recency_days=30,
        max_cases_per_dossier=1,
        jurisdiction_mode="single",
    )

    plans = builder.generate_plans(candidates=candidates, criteria=criteria, reference_time=now)

    assert len(plans) == 1
    assert plans[0].cases[0].case_id == "case-high"
    assert plans[0].plan_id.startswith("dossier-us-ca-")


def test_build_and_enqueue_persists_queue(tmp_path) -> None:
    db_path = tmp_path / "queue.db"
    queue_store = DossierQueueStore(db_path=db_path)
    builder = BundleBuilder(queue_store=queue_store, shared_drive_parent_id="drive-folder-123")
    now = datetime(2025, 12, 3, tzinfo=timezone.utc)
    candidates = [
        DossierCandidate(
            case_id="case-1",
            loss_amount_usd=Decimal("90000"),
            accepted_at=now,
            jurisdiction="US-NY",
        ),
        DossierCandidate(
            case_id="case-2",
            loss_amount_usd=Decimal("125000"),
            accepted_at=now,
            jurisdiction="US-NY",
        ),
    ]
    criteria = BundleCriteria(
        min_loss_usd=Decimal("50000"),
        recency_days=30,
        max_cases_per_dossier=2,
        jurisdiction_mode="single",
    )

    enqueued = builder.build_and_enqueue(candidates=candidates, criteria=criteria)

    assert len(enqueued) == 1
    pending = queue_store.list_pending()
    assert len(pending) == 1
    payload = pending[0]["payload"]
    assert payload["shared_drive_parent_id"] == "drive-folder-123"
    assert len(payload["cases"]) == 2


def test_bundle_candidate_provider_prefers_metrics_view() -> None:
    review_store = _MetricsReviewStore()
    provider = BundleCandidateProvider(
        review_store=review_store,
        structured_store=_StubStructuredStore(),
    )

    candidates = provider.list_candidates(limit=5)

    assert review_store.view_calls == 1
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.case_id == "case-accepted"
    assert candidate.loss_amount_usd == Decimal("75000")
    assert candidate.cross_border is True
    assert candidate.jurisdiction == "US-CA"
    assert candidate.primary_entities[0] == "wallet:abc"


def test_bundle_candidate_provider_fallbacks_to_queue() -> None:
    review_store = _EmptyViewReviewStore()
    provider = BundleCandidateProvider(
        review_store=review_store,
        structured_store=_StubStructuredStore(),
    )

    candidates = provider.list_candidates(limit=5)

    assert review_store.view_calls == 1
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.case_id == "case-accepted"
    assert candidate.loss_amount_usd == Decimal("65000")
    assert candidate.cross_border is True


class _MemoryQueueStore:
    """Minimal in-memory queue stub for builder tests."""

    def __init__(self) -> None:
        self.plan_ids: List[str] = []

    def enqueue_plan(self, plan, *, priority: str = "normal") -> str:  # noqa: D401 - used as a stub
        self.plan_ids.append(plan.plan_id)
        return plan.plan_id


class _MetricsReviewStore:
    def __init__(self) -> None:
        self.view_calls = 0

    def list_dossier_candidates(self, status: str = "accepted", limit: int = 200) -> List[Dict[str, Any]]:  # noqa: D401
        assert status == "accepted"
        self.view_calls += 1
        return [
            {
                "case_id": "case-accepted",
                "accepted_at": "2025-12-02T12:00:00+00:00",
                "loss_amount_usd": "75000",
                "jurisdiction": "US-CA",
                "cross_border": 1,
            }
        ]

    def get_queue(self, status: str = "queued", limit: int = 25) -> List[Dict[str, Any]]:  # noqa: D401 - stub
        raise AssertionError("metrics view should prevent queue fallback")


class _StubReviewStore:
    def get_queue(self, status: str = "queued", limit: int = 25) -> List[Dict[str, Any]]:  # noqa: D401 - stub interface
        assert status == "accepted"
        return [
            {
                "case_id": "case-accepted",
                "last_updated": "2025-12-02T12:00:00+00:00",
            }
        ]


class _EmptyViewReviewStore(_StubReviewStore):
    def __init__(self) -> None:
        self.view_calls = 0

    def list_dossier_candidates(self, status: str = "accepted", limit: int = 200) -> List[Dict[str, Any]]:  # noqa: D401
        assert status == "accepted"
        self.view_calls += 1
        return []


@dataclass
class _StubRecord:
    metadata: Dict[str, Any]
    entities: Dict[str, List[str]]


class _StubStructuredStore:
    def get_by_id(self, case_id: str) -> _StubRecord:  # noqa: D401 - stub interface
        assert case_id == "case-accepted"
        return _StubRecord(
            metadata={
                "loss_amount_usd": 65000,
                "jurisdiction": "US-CA",
                "victim_country": "US",
                "scammer_country": "CN",
            },
            entities={"wallets": ["wallet:abc"], "emails": ["foo@example.com"]},
        )
