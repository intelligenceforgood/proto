"""Tests for the dossier reports API."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from i4g.api.app import create_app
from i4g.reports.bundle_builder import DossierCandidate, DossierPlan
from i4g.store.dossier_queue_store import DossierQueueStore


def _sample_plan(plan_id: str = "test-plan-001") -> DossierPlan:
    candidate = DossierCandidate(
        case_id="case-1",
        loss_amount_usd=Decimal("125000"),
        accepted_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        jurisdiction="US-CA",
        cross_border=True,
        primary_entities=("wallet:test",),
    )
    return DossierPlan(
        plan_id=plan_id,
        jurisdiction_key="US-CA",
        created_at=datetime(2025, 12, 2, tzinfo=timezone.utc),
        total_loss_usd=Decimal("125000"),
        cases=[candidate],
        bundle_reason="unit-test",
        cross_border=True,
        shared_drive_parent_id="drive-folder",
    )


@pytest.fixture()
def queue_store(tmp_path) -> DossierQueueStore:
    return DossierQueueStore(db_path=tmp_path / "dossier_queue.db")


def test_list_dossiers_returns_manifest_and_signature(tmp_path, queue_store, monkeypatch) -> None:
    from i4g.api import reports as reports_api

    plan = _sample_plan()
    queue_store.enqueue_plan(plan)
    queue_store.mark_complete(plan.plan_id, warnings=["pilot-warning"])

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifact_dir / f"{plan.plan_id}.json"
    signature_path = artifact_dir / f"{plan.plan_id}.signatures.json"

    manifest_payload = {
        "plan_id": plan.plan_id,
        "signature_manifest": {"path": str(signature_path), "algorithm": "sha256"},
        "assets": {"timeline_chart": "chart.png"},
    }
    manifest_path.write_text(json.dumps(manifest_payload))
    signature_payload = {
        "algorithm": "sha256",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": [{"label": "manifest", "path": str(manifest_path), "size_bytes": 128, "hash": "abc"}],
        "warnings": [],
    }
    signature_path.write_text(json.dumps(signature_payload))

    monkeypatch.setattr(reports_api, "build_dossier_queue_store", lambda: queue_store)
    monkeypatch.setattr(reports_api, "ARTIFACTS_DIR", artifact_dir)

    client = TestClient(create_app())
    response = client.get("/reports/dossiers", params={"status": "completed", "include_manifest": True})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    record = body["items"][0]
    assert record["plan_id"] == plan.plan_id
    assert record["manifest"]["plan_id"] == plan.plan_id
    assert record["signature_manifest"]["algorithm"] == "sha256"
    assert record["signature_manifest"]["artifacts"][0]["label"] == "manifest"
    assert record["artifact_warnings"] == []


def test_list_dossiers_handles_missing_manifest(tmp_path, queue_store, monkeypatch) -> None:
    from i4g.api import reports as reports_api

    plan = _sample_plan(plan_id="plan-missing-manifest")
    queue_store.enqueue_plan(plan)
    queue_store.mark_complete(plan.plan_id, warnings=[])

    monkeypatch.setattr(reports_api, "build_dossier_queue_store", lambda: queue_store)
    monkeypatch.setattr(reports_api, "ARTIFACTS_DIR", tmp_path / "missing")

    client = TestClient(create_app())
    response = client.get("/reports/dossiers", params={"status": "completed"})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    record = body["items"][0]
    assert record["manifest"] is None
    assert record["signature_manifest"] is None
    assert any("Manifest missing" in warning for warning in record["artifact_warnings"])
