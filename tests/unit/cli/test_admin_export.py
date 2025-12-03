from __future__ import annotations

import json
from types import SimpleNamespace

from i4g.cli import admin


class _StubReviewStore:
    def __init__(self, records: list[dict[str, object]]):
        self._records = records

    def list_saved_searches(self, owner=None, limit=100):  # noqa: D401 - signature matches usage
        return self._records


def test_export_saved_searches_injects_schema_version(tmp_path, monkeypatch):
    records = [
        {
            "name": "High Risk",
            "owner": "analyst",
            "params": {"query": "romance"},
            "tags": None,
        }
    ]
    store = _StubReviewStore(records)
    monkeypatch.setattr(admin, "build_review_store", lambda: store)

    output_path = tmp_path / "saved.json"
    args = SimpleNamespace(
        limit=10,
        all=False,
        owner=None,
        output=str(output_path),
        split=False,
        include_tags=None,
        schema_version="hybrid-v1",
    )

    admin.export_saved_searches(args)

    payload = json.loads(output_path.read_text())
    assert payload[0]["params"]["schema_version"] == "hybrid-v1"
    assert payload[0]["tags"] == []
