"""Tests for dossier generator + queue processor scaffolding."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from i4g.reports.bundle_builder import DossierCandidate, DossierPlan
from i4g.reports.dossier_context import CaseContext, DossierContextResult
from i4g.reports.dossier_pipeline import DossierGenerator
from i4g.reports.dossier_queue_processor import DossierQueueProcessor
from i4g.store.dossier_queue_store import DossierQueueStore


def _sample_plan(plan_id: str = "dossier-us-ca-20251203-01") -> DossierPlan:
    candidate = DossierCandidate(
        case_id="case-1",
        loss_amount_usd=Decimal("100000"),
        accepted_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        jurisdiction="US-CA",
        cross_border=False,
        primary_entities=("wallet:abc",),
    )
    return DossierPlan(
        plan_id=plan_id,
        jurisdiction_key="US-CA",
        created_at=datetime(2025, 12, 3, tzinfo=timezone.utc),
        total_loss_usd=Decimal("100000"),
        cases=[candidate],
        bundle_reason="test-plan",
        cross_border=False,
        shared_drive_parent_id=None,
    )


def test_dossier_generator_writes_manifest(tmp_path) -> None:
    artifact_dir = tmp_path / "artifacts"
    generator = DossierGenerator(artifact_dir=artifact_dir, context_loader=_StaticContextLoader())
    plan = _sample_plan()

    result = generator.generate_from_plan(plan)

    output = Path(result.artifacts[0])
    assert output.exists()
    payload = json.loads(output.read_text())
    assert payload["plan_id"] == plan.plan_id
    assert payload["case_count"] == 1
    analysis = payload["analysis"]
    assert analysis["case_count"] == 1
    assert analysis["loss_by_jurisdiction"]["US-CA"] == "100000"
    assert analysis["primary_entity_frequencies"][0]["entity"] == "wallet:abc"
    agent_payload = payload["agent_payload"]
    assert agent_payload["plan"]["plan_id"] == plan.plan_id
    assert agent_payload["analysis"]["case_count"] == 1
    assert payload["context"]["cases"][0]["structured_record"]["case_id"] == "case-1"
    assert payload["context"]["warnings"] == ["case-warning"]
    assets = payload["assets"]
    timeline_path = (artifact_dir / assets["timeline_chart"]).resolve()
    assert timeline_path.exists()
    geojson_path = (artifact_dir / assets["geojson"]).resolve()
    assert geojson_path.exists()
    geo_map_path = (artifact_dir / assets["geo_map_image"]).resolve()
    assert geo_map_path.exists()
    template_render = payload["template_render"]
    assert template_render["path"].endswith(".md")
    markdown_path = (artifact_dir / template_render["path"]).resolve()
    assert markdown_path.exists()
    signature_info = payload["signature_manifest"]
    signature_path = (artifact_dir / signature_info["path"]).resolve()
    assert signature_info["algorithm"] == "sha256"
    assert signature_path.exists()
    signature_payload = json.loads(signature_path.read_text())
    assert signature_payload["algorithm"] == "sha256"
    assert signature_payload["artifacts"][0]["label"] == "manifest"
    assert signature_payload["artifacts"][0]["path"].endswith(f"{plan.plan_id}.json")
    assert any(Path(path).suffix == ".md" for path in result.artifacts)
    assert result.warnings == ["case-warning"]


def test_processor_completes_and_marks_queue(tmp_path) -> None:
    queue_store = DossierQueueStore(db_path=tmp_path / "queue.db")
    plan = _sample_plan()
    queue_store.enqueue_plan(plan)

    generator = DossierGenerator(artifact_dir=tmp_path / "artifacts", context_loader=_EmptyContextLoader())
    processor = DossierQueueProcessor(queue_store=queue_store, generator=generator)

    summary = processor.process_batch(batch_size=2)

    assert summary.completed == 1
    entry = queue_store.get_plan(plan.plan_id)
    assert entry and entry["status"] == "completed"
    assert entry["warnings"] == []
    artifact_paths = [Path(path) for path in summary.plans[0]["artifacts"]]
    assert len(artifact_paths) == 3
    assert all(path.exists() for path in artifact_paths)


def test_processor_dry_run_restores_pending(tmp_path) -> None:
    queue_store = DossierQueueStore(db_path=tmp_path / "queue.db")
    plan = _sample_plan()
    queue_store.enqueue_plan(plan)

    processor = DossierQueueProcessor(
        queue_store=queue_store,
        generator=DossierGenerator(artifact_dir=tmp_path / "artifacts", context_loader=_EmptyContextLoader()),
    )

    summary = processor.process_batch(batch_size=1, dry_run=True)

    assert summary.processed == 1
    assert summary.completed == 0
    entry = queue_store.get_plan(plan.plan_id)
    assert entry and entry["status"] == "pending"


def test_processor_marks_failures(tmp_path) -> None:
    queue_store = DossierQueueStore(db_path=tmp_path / "queue.db")
    plan = _sample_plan()
    queue_store.enqueue_plan(plan)

    class _FailingGenerator:
        def generate_from_plan(self, plan: DossierPlan):  # noqa: D401 - simple stub
            raise RuntimeError("generation failed")

    processor = DossierQueueProcessor(queue_store=queue_store, generator=_FailingGenerator())

    summary = processor.process_batch(batch_size=1)

    assert summary.failed == 1
    entry = queue_store.get_plan(plan.plan_id)
    assert entry and entry["status"] == "failed"
    assert entry["error"] == "generation failed"


def test_processor_persists_warnings(tmp_path) -> None:
    queue_store = DossierQueueStore(db_path=tmp_path / "queue.db")
    plan = _sample_plan()
    queue_store.enqueue_plan(plan)

    generator = DossierGenerator(artifact_dir=tmp_path / "artifacts", context_loader=_StaticContextLoader())
    processor = DossierQueueProcessor(queue_store=queue_store, generator=generator)

    summary = processor.process_batch(batch_size=1)

    assert summary.completed == 1
    entry = queue_store.get_plan(plan.plan_id)
    assert entry and entry["warnings"] == ["case-warning"]


class _StaticContextLoader:
    def load(self, plan: DossierPlan) -> DossierContextResult:  # noqa: D401 - stub helper
        contexts = [
            CaseContext(
                case_id=case.case_id,
                structured_record={"case_id": case.case_id, "text": "context-text"},
                review={"review_id": f"review-{case.case_id}"},
                warnings=("case-warning",),
            )
            for case in plan.cases
        ]
        return DossierContextResult(cases=tuple(contexts), warnings=("case-warning",))


class _EmptyContextLoader:
    def load(self, plan: DossierPlan) -> DossierContextResult:  # noqa: D401 - stub helper
        contexts = [
            CaseContext(
                case_id=case.case_id,
                structured_record=None,
                review=None,
                warnings=tuple(),
            )
            for case in plan.cases
        ]
        return DossierContextResult(cases=tuple(contexts), warnings=tuple())
