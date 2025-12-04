"""Cloud Run job entrypoint for processing dossier queue entries."""

from __future__ import annotations

import logging
import os
import sys

from i4g.reports.dossier_queue_processor import DossierQueueProcessor, QueueProcessSummary
from i4g.task_status import TaskStatusReporter

LOGGER = logging.getLogger("i4g.worker.jobs.dossier_queue")


def _configure_logging() -> None:
    level_name = os.getenv("I4G_RUNTIME__LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def run_job(
    *,
    batch_size: int,
    dry_run: bool,
    processor: DossierQueueProcessor | None = None,
    reporter: TaskStatusReporter | None = None,
) -> QueueProcessSummary:
    """Run a single processor batch and return the summary (test helper)."""

    runner = processor or DossierQueueProcessor()
    return runner.process_batch(batch_size=batch_size, dry_run=dry_run, reporter=reporter)


def main() -> int:
    """Entry point executed by Cloud Run jobs and local CLI."""

    _configure_logging()
    batch_size = int(os.getenv("I4G_DOSSIER__BATCH_SIZE", "5") or 5)
    dry_run = _env_bool("I4G_DOSSIER__DRY_RUN", default=False)

    LOGGER.info("Starting dossier queue job: batch_size=%s dry_run=%s", batch_size, dry_run)
    reporter = TaskStatusReporter()
    if reporter.is_enabled():
        reporter.update(status="started", message="Dossier job started", batch_size=batch_size, dry_run=dry_run)

    summary = run_job(batch_size=batch_size, dry_run=dry_run, reporter=reporter if reporter.is_enabled() else None)

    LOGGER.info(
        "Dossier queue job complete: processed=%s completed=%s failed=%s dry_run=%s",
        summary.processed,
        summary.completed,
        summary.failed,
        summary.dry_run,
    )

    if reporter.is_enabled():
        reporter.update(
            status="finished" if summary.failed == 0 else "partial",
            message="Dossier job complete",
            processed=summary.processed,
            completed=summary.completed,
            failed=summary.failed,
            dry_run=summary.dry_run,
        )

    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
