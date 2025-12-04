"""Tests for the dossier queue worker job."""

from __future__ import annotations

from dataclasses import dataclass

from i4g.reports.dossier_queue_processor import QueueProcessSummary
from i4g.worker.jobs import dossier_queue


@dataclass
class _StubProcessor:
    processed: int
    completed: int
    failed: int

    def process_batch(self, *, batch_size: int, dry_run: bool):  # noqa: D401 - stub helper
        assert batch_size == self.processed
        summary = QueueProcessSummary(
            processed=self.processed,
            completed=self.completed,
            failed=self.failed,
            dry_run=dry_run,
            plans=[],
        )
        return summary


def test_run_job_delegates_to_processor() -> None:
    stub = _StubProcessor(processed=2, completed=2, failed=0)

    summary = dossier_queue.run_job(batch_size=2, dry_run=False, processor=stub)

    assert summary.completed == 2
    assert summary.failed == 0


def test_main_returns_error_code_when_failures(monkeypatch) -> None:
    stub = _StubProcessor(processed=1, completed=0, failed=1)

    monkeypatch.setenv("I4G_DOSSIER__BATCH_SIZE", "1")
    monkeypatch.setenv("I4G_DOSSIER__DRY_RUN", "false")
    monkeypatch.setenv("I4G_RUNTIME__LOG_LEVEL", "CRITICAL")
    monkeypatch.setattr(
        dossier_queue, "run_job", lambda batch_size, dry_run: stub.process_batch(batch_size=batch_size, dry_run=dry_run)
    )

    exit_code = dossier_queue.main()

    assert exit_code == 1


def test_main_success(monkeypatch) -> None:
    stub = _StubProcessor(processed=1, completed=1, failed=0)

    monkeypatch.setenv("I4G_DOSSIER__BATCH_SIZE", "1")
    monkeypatch.setenv("I4G_DOSSIER__DRY_RUN", "true")
    monkeypatch.setattr(
        dossier_queue, "run_job", lambda batch_size, dry_run: stub.process_batch(batch_size=batch_size, dry_run=dry_run)
    )

    exit_code = dossier_queue.main()

    assert exit_code == 0
