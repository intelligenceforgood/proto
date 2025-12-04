"""Unit tests for the dossier context loader helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable

from i4g.reports.bundle_builder import DossierCandidate, DossierPlan
from i4g.reports.dossier_context import DossierContextLoader
from i4g.store.schema import ScamRecord


def test_loader_hydrates_structured_and_review_context() -> None:
    plan = _plan()
    structured_store = _StubStructuredStore(
        {
            "case-123": ScamRecord(
                case_id="case-123",
                text="Sample text",
                entities={"wallets": ["wallet:abc"]},
                classification="crypto",
                confidence=0.92,
                metadata={"loss_amount_usd": 90000},
                created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
            )
        }
    )
    review_store = _StubReviewStore(
        {
            "case-123": {
                "review_id": "review-1",
                "case_id": "case-123",
                "status": "accepted",
                "priority": "medium",
                "queued_at": "2025-12-01T00:00:00+00:00",
                "last_updated": "2025-12-02T00:00:00+00:00",
            }
        }
    )

    loader = DossierContextLoader(structured_store=structured_store, review_store=review_store)

    result = loader.load(plan)

    assert len(result.cases) == 1
    case_context = result.cases[0]
    assert case_context.structured_record is not None
    assert case_context.structured_record["case_id"] == "case-123"
    assert case_context.review["review_id"] == "review-1"
    assert case_context.warnings == ()
    assert result.warnings == ()


def test_loader_emits_warnings_for_missing_data() -> None:
    plan = _plan(case_id="missing-case")
    loader = DossierContextLoader(
        structured_store=_StubStructuredStore({}),
        review_store=_StubReviewStore({}),
    )

    result = loader.load(plan)

    assert "No structured record found for case missing-case" in result.warnings
    assert "No review metadata found for case missing-case" in result.warnings
    assert result.cases[0].warnings == tuple(result.warnings)


def _plan(case_id: str = "case-123") -> DossierPlan:
    candidate = DossierCandidate(
        case_id=case_id,
        loss_amount_usd=Decimal("100000"),
        accepted_at=datetime(2025, 12, 2, tzinfo=timezone.utc),
        jurisdiction="US-CA",
        cross_border=False,
        primary_entities=("wallet:abc",),
    )
    return DossierPlan(
        plan_id="plan-1",
        jurisdiction_key="US-CA",
        created_at=datetime(2025, 12, 3, tzinfo=timezone.utc),
        total_loss_usd=Decimal("100000"),
        cases=[candidate],
        bundle_reason="test",
        cross_border=False,
        shared_drive_parent_id=None,
    )


class _StubStructuredStore:
    def __init__(self, records: Dict[str, ScamRecord]) -> None:
        self._records = records

    def get_by_id(self, case_id: str) -> ScamRecord | None:  # noqa: D401 - stub helper
        return self._records.get(case_id)


class _StubReviewStore:
    def __init__(self, rows: Dict[str, Dict[str, Any]]) -> None:
        self._rows = rows

    def get_cases(self, case_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:  # noqa: D401 - stub helper
        return {case_id: self._rows[case_id] for case_id in case_ids if case_id in self._rows}
