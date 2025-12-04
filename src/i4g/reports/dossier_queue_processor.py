"""Utilities for leasing dossier queue entries and generating artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from i4g.reports.bundle_builder import DossierPlan
from i4g.reports.dossier_pipeline import DossierGenerationResult, DossierGenerator
from i4g.services.factories import build_dossier_queue_store
from i4g.store.dossier_queue_store import DossierQueueStore
from i4g.task_status import TaskStatusReporter


@dataclass
class QueueProcessSummary:
    """Aggregate statistics describing a processor run."""

    processed: int
    completed: int
    failed: int
    dry_run: bool
    plans: List[dict] = field(default_factory=list)


class DossierQueueProcessor:
    """Leases queued dossier plans and renders artifacts via :class:`DossierGenerator`."""

    def __init__(
        self,
        *,
        queue_store: DossierQueueStore | None = None,
        generator: DossierGenerator | None = None,
    ) -> None:
        self._queue_store = queue_store or build_dossier_queue_store()
        self._generator = generator or DossierGenerator()

    def process_batch(
        self,
        *,
        batch_size: int = 5,
        dry_run: bool = False,
        reporter: TaskStatusReporter | None = None,
    ) -> QueueProcessSummary:
        """Process up to ``batch_size`` queue entries and return the execution summary."""

        processed = 0
        completed = 0
        failed = 0
        plan_summaries: List[dict] = []

        for _ in range(batch_size):
            leased = self._queue_store.lease_next()
            if not leased:
                break
            processed += 1
            plan_payload = leased.get("payload") or {}
            plan = DossierPlan.from_dict(plan_payload)
            plan_id = plan.plan_id
            if reporter:
                reporter.update(
                    status="leased",
                    message=f"Processing dossier plan {plan_id}",
                    plan_id=plan_id,
                    processed=processed,
                    completed=completed,
                    failed=failed,
                )

            if dry_run:
                self._queue_store.reset(plan_id)
                plan_summaries.append({"plan_id": plan_id, "status": "dry-run"})
                if reporter:
                    reporter.update(
                        status="dry_run",
                        message=f"Inspected dossier plan {plan_id}",
                        plan_id=plan_id,
                    )
                continue

            try:
                result = self._generator.generate_from_plan(plan)
                self._queue_store.mark_complete(plan_id, warnings=result.warnings)
                plan_summaries.append(self._result_summary(result, status="completed"))
                completed += 1
                if reporter:
                    reporter.update(
                        status="completed",
                        message=f"Generated dossier for plan {plan_id}",
                        plan_id=plan_id,
                        artifacts=[str(path) for path in result.artifacts],
                        warnings=list(result.warnings),
                        case_count=len(plan.cases),
                        total_loss_usd=str(plan.total_loss_usd),
                    )
            except Exception as exc:  # pragma: no cover - defensive logging
                self._queue_store.mark_failed(plan_id, error=str(exc))
                plan_summaries.append({"plan_id": plan_id, "status": "failed", "error": str(exc)})
                failed += 1
                if reporter:
                    reporter.update(
                        status="failed",
                        message=f"Dossier plan {plan_id} failed",
                        plan_id=plan_id,
                        error=str(exc),
                    )

        summary = QueueProcessSummary(
            processed=processed,
            completed=completed,
            failed=failed,
            dry_run=dry_run,
            plans=plan_summaries,
        )
        if reporter:
            reporter.update(
                status="finished" if failed == 0 else "partial",
                message="Dossier batch complete",
                processed=processed,
                completed=completed,
                failed=failed,
                dry_run=dry_run,
            )
        return summary

    def _result_summary(self, result: DossierGenerationResult, *, status: str) -> dict:
        return {
            "plan_id": result.plan_id,
            "status": status,
            "artifacts": [str(path) for path in result.artifacts],
            "warnings": list(result.warnings),
        }
