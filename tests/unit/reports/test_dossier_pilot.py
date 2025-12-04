"""Tests for dossier pilot helper flows."""

from __future__ import annotations

import json
from decimal import Decimal

from i4g.reports.bundle_builder import BundleBuilder, BundleCriteria
from i4g.reports.bundle_candidates import BundleCandidateProvider
from i4g.reports.dossier_pilot import PilotCaseSpec, load_pilot_case_specs, schedule_pilot_plans, seed_pilot_cases
from i4g.store.dossier_queue_store import DossierQueueStore
from i4g.store.review_store import ReviewStore
from i4g.store.structured import StructuredStore


def _pilot_payload(case_id: str = "case-pilot-test") -> dict:
    return {
        "case_id": case_id,
        "text": "Victim moved funds into suspect wallet after staged romance.",
        "classification": "romance_investment",
        "confidence": 0.91,
        "loss_amount_usd": 125000,
        "jurisdiction": "US-CA",
        "victim_country": "US",
        "offender_country": "NG",
        "accepted_at": "2025-11-18T12:00:00Z",
        "entities": {"emails": ["pilot@example.com"]},
    }


def test_load_pilot_case_specs(tmp_path) -> None:
    payload = [_pilot_payload(), _pilot_payload("case-pilot-alt")]
    config_path = tmp_path / "pilot.json"
    config_path.write_text(json.dumps(payload))

    specs = load_pilot_case_specs(config_path)

    assert len(specs) == 2
    assert specs[0].case_id == "case-pilot-test"
    assert specs[0].victim_country == "US"
    assert specs[1].case_id == "case-pilot-alt"


def test_seed_pilot_cases_creates_structured_and_queue_entries(tmp_path) -> None:
    db_path = tmp_path / "pilot.db"
    review_store = ReviewStore(str(db_path))
    structured_store = StructuredStore(db_path=db_path)
    spec = PilotCaseSpec.from_dict(_pilot_payload())

    summary = seed_pilot_cases([spec], review_store=review_store, structured_store=structured_store)

    assert summary.case_ids == (spec.case_id,)
    record = structured_store.get_by_id(spec.case_id)
    assert record is not None
    assert record.metadata["loss_amount_usd"] == float(spec.loss_amount_usd)
    queue_entries = review_store.get_cases([spec.case_id])
    assert spec.case_id in queue_entries
    entry = queue_entries[spec.case_id]
    assert entry["status"] == "accepted"
    assert entry["priority"] == "pilot"
    structured_store.close()


def test_schedule_pilot_plans_enqueues_queue(tmp_path) -> None:
    db_path = tmp_path / "pilot.db"
    review_store = ReviewStore(str(db_path))
    structured_store = StructuredStore(db_path=db_path)
    spec = PilotCaseSpec.from_dict(_pilot_payload())
    seed_pilot_cases([spec], review_store=review_store, structured_store=structured_store)

    queue_store = DossierQueueStore(db_path=db_path)
    builder = BundleBuilder(queue_store=queue_store)
    provider = BundleCandidateProvider(review_store=review_store, structured_store=structured_store)
    criteria = BundleCriteria(
        min_loss_usd=Decimal("50000"),
        recency_days=60,
        max_cases_per_dossier=3,
        jurisdiction_mode="single",
        require_cross_border=False,
    )

    dry_summary = schedule_pilot_plans(
        [spec],
        bundle_builder=builder,
        candidate_provider=provider,
        criteria=criteria,
        dry_run=True,
    )
    assert dry_summary.dry_run is True
    assert dry_summary.plan_ids

    live_summary = schedule_pilot_plans(
        [spec],
        bundle_builder=builder,
        candidate_provider=provider,
        criteria=criteria,
        dry_run=False,
    )
    assert live_summary.plan_ids
    plan_record = queue_store.get_plan(live_summary.plan_ids[0])
    assert plan_record is not None
    assert plan_record["plan_id"] == live_summary.plan_ids[0]
    structured_store.close()
