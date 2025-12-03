"""Unit tests for the HybridSearchService orchestration layer."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from i4g.services.hybrid_search import HybridSearchQuery, HybridSearchService, QueryEntityFilter, QueryTimeRange
from i4g.settings import reload_settings


class _StubRetriever:
    """Test double that captures query arguments."""

    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.last_filters = None

    def query(self, **kwargs):  # type: ignore[override]
        self.last_filters = kwargs.get("filters")
        return self.payload


class _SpyObservability:
    """Captures observability interactions for assertions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.counters: list[tuple[str, float, dict[str, str] | None]] = []
        self.timings: list[tuple[str, float, dict[str, str] | None]] = []

    def emit_event(self, event: str, **fields: object) -> None:
        self.events.append((event, dict(fields)))

    def increment(self, metric: str, *, value: float = 1.0, tags: dict[str, str] | None = None) -> None:
        self.counters.append((metric, value, tags))

    def record_timing(self, metric: str, value_ms: float, *, tags: dict[str, str] | None = None) -> None:
        self.timings.append((metric, value_ms, tags))


class _StubEntityStore:
    """Minimal entity-store double for schema helpers."""

    def __init__(self, *, datasets: list[str] | None = None, examples: dict[str, list[dict[str, str]]] | None = None):
        self._datasets = datasets or []
        self._examples = examples or {}

    def list_datasets(self, *, entity_types=None, limit=None):  # type: ignore[override]
        return list(self._datasets)

    def list_entity_examples(self, *, entity_types, datasets=None, per_type_limit=5):  # type: ignore[override]
        return self._examples


@pytest.fixture(name="retriever_payload")
def _retriever_payload() -> dict[str, object]:
    return {
        "results": [
            {
                "case_id": "case-001",
                "sources": ["vector", "structured"],
                "vector": {"similarity": 0.9},
                "record": {
                    "case_id": "case-001",
                    "confidence": 0.8,
                    "created_at": datetime.utcnow().isoformat(),
                    "classification": "romance",
                    "metadata": {"dataset": "retrieval_poc_dev"},
                },
            },
            {
                "case_id": "case-002",
                "sources": ["structured"],
                "record": {
                    "case_id": "case-002",
                    "confidence": 0.6,
                    "created_at": (datetime.utcnow() - timedelta(days=10)).isoformat(),
                    "classification": "pig_butcher",
                    "metadata": {},
                },
            },
        ],
        "vector_hits": 1,
        "structured_hits": 2,
        "total": 2,
    }


def test_search_merges_scores_and_applies_time_range(retriever_payload):
    retriever = _StubRetriever(retriever_payload)
    start = datetime.utcnow() - timedelta(days=1)
    end = datetime.utcnow() + timedelta(days=1)
    query = HybridSearchQuery(
        text="romance scam",
        time_range=QueryTimeRange(start=start, end=end),
        limit=5,
    )
    service = HybridSearchService(retriever=retriever, entity_store=_StubEntityStore())

    response = service.search(query)

    assert response["count"] == 1  # second record filtered out by time window
    item = response["results"][0]
    assert item["case_id"] == "case-001"
    assert item["merged_score"] is not None
    assert item["scores"]["winner"] == "semantic"
    semantic_weighted = item["scores"]["semantic_weighted"]
    structured_weighted = item["scores"].get("structured_weighted", 0)
    assert semantic_weighted > structured_weighted
    assert response["vector_hits"] == 1
    assert response["structured_hits"] == 2
    diagnostics = response.get("diagnostics")
    assert diagnostics["score_policy"]["strategy"] == "max_weighted"
    assert diagnostics["score_policy"]["tie_breaker"] == HybridSearchService.TIE_BREAKER
    assert diagnostics["counts"]["returned_results"] == 1


def test_entity_filters_pass_through_to_retriever(retriever_payload):
    retriever = _StubRetriever(retriever_payload)
    query = HybridSearchQuery(
        entities=[QueryEntityFilter(type="bank_account", value="123", match_mode="exact")],
        classifications=["romance"],
        datasets=["retrieval_poc_dev"],
        loss_buckets=[">50k"],
        case_ids=["case-xyz"],
    )
    service = HybridSearchService(retriever=retriever, entity_store=_StubEntityStore())

    service.search(query)

    assert retriever.last_filters == [
        ("classification", "romance"),
        ("dataset", "retrieval_poc_dev"),
        ("case_id", "case-xyz"),
        (
            "bank_account",
            {
                "filter_type": "entity",
                "entity_type": "bank_account",
                "value": "123",
                "match_mode": "exact",
                "datasets": ["retrieval_poc_dev"],
                "loss_buckets": [">50k"],
            },
        ),
    ]


def test_search_emits_observability_signals(retriever_payload):
    retriever = _StubRetriever(retriever_payload)
    spy = _SpyObservability()
    query = HybridSearchQuery(text="romance scam", limit=5)
    service = HybridSearchService(retriever=retriever, observability=spy, entity_store=_StubEntityStore())

    service.search(query)

    counter_names = {entry[0] for entry in spy.counters}
    assert "hybrid_search.query.total" in counter_names
    assert any(metric == "hybrid_search.query.duration_ms" for metric, _, _ in spy.timings)
    assert spy.events and spy.events[-1][0] == "hybrid_search.query"


def test_schema_reflects_search_settings_and_caches():
    settings = reload_settings()
    custom_search = settings.search.model_copy(
        update={
            "indicator_types": ["ip_address"],
            "dataset_presets": ["retrieval_poc_dev"],
            "classification_presets": ["romance"],
            "loss_buckets": [">50k"],
            "time_presets": ["30d"],
            "schema_cache_ttl_seconds": 60,
        }
    )
    settings = settings.model_copy(update={"search": custom_search})
    retriever = _StubRetriever({"results": [], "vector_hits": 0, "structured_hits": 0, "total": 0})
    entity_store = _StubEntityStore(
        datasets=["network_smoke"],
        examples={"ip_address": [{"value": "203.0.113.25"}]},
    )
    service = HybridSearchService(retriever=retriever, settings=settings, entity_store=entity_store)

    schema = service.schema()
    assert schema == {
        "indicator_types": ["ip_address"],
        "datasets": ["retrieval_poc_dev", "network_smoke"],
        "classifications": ["romance"],
        "loss_buckets": [">50k"],
        "time_presets": ["30d"],
        "entity_examples": {"ip_address": ["203.0.113.25"]},
    }
    # Access again to ensure cache path is exercised (no assertion, but should not raise)
    assert service.schema() == schema


def test_weighted_scores_control_result_ordering():
    payload = {
        "results": [
            {
                "case_id": "semantic-first",
                "sources": ["vector"],
                "vector": {"similarity": 0.95},
                "record": {
                    "case_id": "semantic-first",
                    "confidence": 0.1,
                    "metadata": {},
                },
            },
            {
                "case_id": "structured-dominant",
                "sources": ["structured"],
                "record": {
                    "case_id": "structured-dominant",
                    "confidence": 0.9,
                    "metadata": {},
                },
            },
        ],
        "vector_hits": 1,
        "structured_hits": 2,
        "total": 2,
    }
    retriever = _StubRetriever(payload)
    settings = reload_settings()
    tuned_search = settings.search.model_copy(update={"semantic_weight": 0.2, "structured_weight": 0.8})
    tuned_settings = settings.model_copy(update={"search": tuned_search})
    service = HybridSearchService(retriever=retriever, settings=tuned_settings, entity_store=_StubEntityStore())

    response = service.search(HybridSearchQuery(limit=5))

    ordered_cases = [item["case_id"] for item in response["results"]]
    assert ordered_cases == ["structured-dominant", "semantic-first"]
    assert response["diagnostics"]["score_policy"]["semantic_weight"] == pytest.approx(0.2)
    assert response["diagnostics"]["score_policy"]["structured_weight"] == pytest.approx(0.8)


def test_diagnostics_capture_overlap_and_time_filter_counts():
    now = datetime.utcnow()
    payload = {
        "results": [
            {
                "case_id": "recent",
                "sources": ["vector", "structured"],
                "vector": {"similarity": 0.9},
                "record": {
                    "case_id": "recent",
                    "confidence": 0.7,
                    "created_at": now.isoformat(),
                    "metadata": {"dataset": "retrieval_poc_dev"},
                },
            },
            {
                "case_id": "stale",
                "sources": ["structured"],
                "record": {
                    "case_id": "stale",
                    "confidence": 0.8,
                    "created_at": (now - timedelta(days=60)).isoformat(),
                    "metadata": {},
                },
            },
        ],
        "vector_hits": 3,
        "structured_hits": 4,
        "total": 2,
    }
    retriever = _StubRetriever(payload)
    query = HybridSearchQuery(
        time_range=QueryTimeRange(start=now - timedelta(days=1), end=now + timedelta(days=1)),
        limit=5,
    )
    service = HybridSearchService(retriever=retriever, entity_store=_StubEntityStore())

    response = service.search(query)

    diagnostics = response["diagnostics"]
    counts = diagnostics["counts"]
    assert counts["merged_results"] == 2
    assert counts["returned_results"] == 1
    assert counts["dropped_by_time_range"] == 1
    assert counts["deduped_overlap"] == 5  # vector_hits + structured_hits - merged_results
    assert counts["source_breakdown"]["merged"] == 1
    assert counts["source_breakdown"]["structured_only"] == 1


def test_weighted_score_ties_prefer_structured_branch():
    payload = {
        "results": [
            {
                "case_id": "tie-case",
                "sources": ["vector", "structured"],
                "vector": {"similarity": 0.9},
                "record": {
                    "case_id": "tie-case",
                    "confidence": 0.9,
                    "metadata": {},
                },
            }
        ],
        "vector_hits": 1,
        "structured_hits": 1,
        "total": 1,
    }
    retriever = _StubRetriever(payload)
    settings = reload_settings()
    tuned_search = settings.search.model_copy(update={"semantic_weight": 0.5, "structured_weight": 0.5})
    tuned_settings = settings.model_copy(update={"search": tuned_search})
    service = HybridSearchService(retriever=retriever, settings=tuned_settings, entity_store=_StubEntityStore())

    response = service.search(HybridSearchQuery(limit=5))

    item = response["results"][0]
    assert item["scores"]["winner"] == "structured"
    assert item["merged_score"] == pytest.approx(0.45)
    assert item["scores"]["semantic_weighted"] == pytest.approx(item["scores"]["structured_weighted"])
    assert item["scores"].get("tie_breaker_applied") is True
    diagnostics = response["diagnostics"]
    assert diagnostics["score_policy"]["tie_breaker_applications"] == 1


def test_score_policy_reports_winner_counts():
    payload = {
        "results": [
            {
                "case_id": "vector-win",
                "sources": ["vector"],
                "vector": {"similarity": 0.95},
                "record": {
                    "case_id": "vector-win",
                    "confidence": 0.2,
                    "metadata": {},
                },
            },
            {
                "case_id": "structured-win",
                "sources": ["structured"],
                "record": {
                    "case_id": "structured-win",
                    "confidence": 0.9,
                    "metadata": {},
                },
            },
        ],
        "vector_hits": 2,
        "structured_hits": 2,
        "total": 2,
    }
    retriever = _StubRetriever(payload)
    settings = reload_settings()
    tuned_search = settings.search.model_copy(update={"semantic_weight": 0.8, "structured_weight": 0.6})
    tuned_settings = settings.model_copy(update={"search": tuned_search})
    service = HybridSearchService(retriever=retriever, settings=tuned_settings, entity_store=_StubEntityStore())

    response = service.search(HybridSearchQuery(limit=5))

    policy = response["diagnostics"]["score_policy"]
    assert policy["winners"]["semantic"] == 1
    assert policy["winners"]["structured"] == 1
    assert policy["winners"]["unknown"] == 0
