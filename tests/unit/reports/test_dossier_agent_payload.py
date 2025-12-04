"""Tests for dossier agent payload helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from i4g.reports.bundle_builder import DossierCandidate, DossierPlan
from i4g.reports.dossier_agent_payload import build_agent_payload
from i4g.reports.dossier_analysis import analyze_plan
from i4g.reports.dossier_context import CaseContext, DossierContextResult


def test_build_agent_payload_combines_sources() -> None:
    candidate = DossierCandidate(
        case_id="case-1",
        loss_amount_usd=Decimal("42000"),
        accepted_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        jurisdiction="US-CA",
        cross_border=True,
        primary_entities=("wallet:abc",),
    )
    plan = DossierPlan(
        plan_id="plan-1",
        jurisdiction_key="US-CA",
        created_at=datetime(2025, 12, 3, tzinfo=timezone.utc),
        total_loss_usd=Decimal("42000"),
        cases=[candidate],
        bundle_reason="test",
        cross_border=True,
        shared_drive_parent_id=None,
    )
    analysis = analyze_plan(plan)
    context = DossierContextResult(
        cases=(
            CaseContext(
                case_id="case-1",
                structured_record=None,
                review=None,
                warnings=tuple(),
            ),
        ),
        warnings=tuple(),
    )

    payload = build_agent_payload(plan=plan, context=context, analysis=analysis).to_dict()

    assert payload["plan"]["plan_id"] == "plan-1"
    assert payload["context"]["cases"][0]["case_id"] == "case-1"
    assert payload["analysis"]["case_count"] == 1
