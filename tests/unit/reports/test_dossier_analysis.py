"""Unit tests for dossier analysis helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from i4g.reports.bundle_builder import DossierCandidate, DossierPlan
from i4g.reports.dossier_analysis import analyze_plan


def test_analyze_plan_computes_summary_metrics() -> None:
    base_time = datetime(2025, 12, 1, tzinfo=timezone.utc)
    candidates = [
        DossierCandidate(
            case_id="case-1",
            loss_amount_usd=Decimal("75000"),
            accepted_at=base_time,
            jurisdiction="US-CA",
            cross_border=True,
            primary_entities=("wallet:a", "email:a@example.com"),
        ),
        DossierCandidate(
            case_id="case-2",
            loss_amount_usd=Decimal("50000"),
            accepted_at=base_time + timedelta(days=2),
            jurisdiction="US-NY",
            cross_border=False,
            primary_entities=("wallet:a", "wallet:b"),
        ),
    ]
    plan = DossierPlan(
        plan_id="plan-abc",
        jurisdiction_key="US",
        created_at=base_time,
        total_loss_usd=Decimal("125000"),
        cases=candidates,
        bundle_reason="test",
        cross_border=True,
        shared_drive_parent_id=None,
    )

    analysis = analyze_plan(plan)
    payload = analysis.to_dict()

    assert payload["case_count"] == 2
    assert payload["total_loss_usd"] == "125000"
    assert payload["loss_by_jurisdiction"]["US-CA"] == "75000"
    assert payload["loss_by_jurisdiction"]["US-NY"] == "50000"
    assert payload["cross_border_cases"] == ["case-1"]
    assert payload["accepted_range"]["earliest"] == base_time.isoformat()
    assert payload["accepted_range"]["latest"] == (base_time + timedelta(days=2)).isoformat()
    freq = payload["primary_entity_frequencies"]
    assert freq[0] == {"entity": "wallet:a", "count": 2}
    assert any(entry["entity"] == "email:a@example.com" for entry in freq)
