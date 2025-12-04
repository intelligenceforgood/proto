"""Lightweight helpers for emitting task-status updates across services."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import requests

LOGGER = logging.getLogger(__name__)


@dataclass
class TaskStatusReporter:
    """Reports task state changes to the API in-memory store or an HTTP endpoint."""

    task_id: Optional[str] = None
    endpoint: Optional[str] = None
    sink: Optional[Callable[[str, Dict[str, Any]], None]] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.task_id is None:
            self.task_id = os.getenv("I4G_TASK_ID")
        if self.endpoint is None:
            self.endpoint = os.getenv("I4G_TASK_STATUS_URL")

    def is_enabled(self) -> bool:
        """Return ``True`` when a task identifier is available for updates."""

        return bool(self.task_id)

    def update(self, *, status: str, message: str, **payload: Any) -> None:
        """Publish a task-status update.

        Args:
            status: Short status string (e.g., ``in_progress``).
            message: Human-readable description of the update.
            **payload: Additional JSON-serializable fields.
        """

        if not self.task_id:
            LOGGER.debug("TaskStatusReporter skipped update (task_id missing): %s - %s", status, message)
            return

        body: Dict[str, Any] = {"status": status, "message": message}
        body.update(payload)

        if self.sink:
            self.sink(self.task_id, body)
            return

        if self.endpoint:
            self._post_update(body)
            return

        if not self._update_local_store(body):
            LOGGER.debug("TaskStatusReporter could not locate a task store; dropping update: %s", json.dumps(body))

    def _post_update(self, body: Dict[str, Any]) -> None:
        url = f"{self.endpoint.rstrip('/')}/{self.task_id}/update"
        try:
            response = requests.post(url, json=body, timeout=5)
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - network/HTTP errors
            LOGGER.warning("Task status POST failed (%s): %s", url, exc)

    def _update_local_store(self, body: Dict[str, Any]) -> bool:
        try:
            from i4g.api.app import TASK_STATUS  # type: ignore import
        except Exception:  # pragma: no cover - import guard
            return False

        TASK_STATUS[self.task_id] = body
        return True


__all__ = ["TaskStatusReporter"]
