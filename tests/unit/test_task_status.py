"""Tests for TaskStatusReporter utilities."""

from __future__ import annotations

from typing import Dict, List, Tuple

from i4g.task_status import TaskStatusReporter


def test_reporter_records_updates_via_sink(monkeypatch) -> None:
    captured: List[Tuple[str, Dict[str, object]]] = []

    def _sink(task_id: str, payload: Dict[str, object]) -> None:
        captured.append((task_id, payload))

    reporter = TaskStatusReporter(task_id="task-123", sink=_sink)

    reporter.update(status="in_progress", message="Processing", processed=1)

    assert captured[0][0] == "task-123"
    assert captured[0][1]["status"] == "in_progress"
    assert captured[0][1]["processed"] == 1


def test_reporter_noops_without_task_id() -> None:
    invoked: List[Tuple[str, Dict[str, object]]] = []

    def _sink(task_id: str, payload: Dict[str, object]) -> None:
        invoked.append((task_id, payload))

    reporter = TaskStatusReporter(task_id=None, sink=_sink)

    reporter.update(status="in_progress", message="Processing")

    assert invoked == []
