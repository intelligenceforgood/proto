"""Hybrid search orchestration layer combining semantic and structured sources."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Sequence

from i4g.observability import Observability, get_observability
from i4g.services.factories import build_entity_store
from i4g.settings import Settings, get_settings
from i4g.store.retriever import HybridRetriever


@dataclass
class QueryTimeRange:
    """Time window applied to hybrid search results."""

    start: datetime
    end: datetime


@dataclass
class QueryEntityFilter:
    """Entity filter describing the indicator type/value to match."""

    type: str
    value: str
    match_mode: Literal["exact", "prefix", "contains"] = "exact"


@dataclass
class HybridSearchQuery:
    """Normalized hybrid search request."""

    text: str | None = None
    entities: List[QueryEntityFilter] = field(default_factory=list)
    classifications: List[str] = field(default_factory=list)
    datasets: List[str] = field(default_factory=list)
    loss_buckets: List[str] = field(default_factory=list)
    case_ids: List[str] = field(default_factory=list)
    time_range: QueryTimeRange | None = None
    limit: int | None = None
    vector_limit: int | None = None
    structured_limit: int | None = None
    offset: int = 0


@dataclass
class HybridSearchItem:
    """Single merged hybrid search result."""

    case_id: str
    sources: List[str]
    merged_score: float | None
    scores: Dict[str, Any]
    record: Dict[str, Any] | None = None
    vector: Dict[str, Any] | None = None
    metadata: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation."""

        payload = asdict(self)
        return payload


@dataclass
class SearchSchema:
    """Schema metadata describing available hybrid search filters."""

    indicator_types: List[str]
    datasets: List[str]
    classifications: List[str]
    loss_buckets: List[str]
    time_presets: List[str]
    entity_examples: Dict[str, List[str]] | None = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize schema to a dictionary."""

        payload = {
            "indicator_types": list(self.indicator_types),
            "datasets": list(self.datasets),
            "classifications": list(self.classifications),
            "loss_buckets": list(self.loss_buckets),
            "time_presets": list(self.time_presets),
        }
        if self.entity_examples:
            payload["entity_examples"] = self.entity_examples
        return payload


class HybridSearchService:
    """Coordinate hybrid search queries across vector + structured stores."""

    SCORE_STRATEGY = "max_weighted"
    TIE_BREAKER = "structured_preference"
    _TIE_EPSILON = 1e-9

    def __init__(
        self,
        *,
        retriever: HybridRetriever | None = None,
        settings: Settings | None = None,
        observability: Observability | None = None,
        entity_store: "EntityStore" | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.retriever = retriever or HybridRetriever()
        self.observability = observability or get_observability(component="hybrid_search", settings=self.settings)
        if entity_store is not None:
            self.entity_store = entity_store
        else:
            candidate = getattr(self.retriever, "entity_store", None)
            self.entity_store = candidate if candidate is not None else build_entity_store()
        self._schema_cache: tuple[float, SearchSchema] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, query: HybridSearchQuery) -> Dict[str, Any]:
        """Execute a hybrid search request and return merged results."""

        limit = query.limit or self.settings.search.default_limit
        vector_top_k = query.vector_limit or limit
        structured_top_k = query.structured_limit or limit
        filters = self._build_filter_items(query)
        metric_tags = self._metric_tags(query)
        self.observability.increment("hybrid_search.query.total", tags=metric_tags)

        start_time = time.perf_counter()
        raw = self.retriever.query(
            text=query.text,
            filters=filters or None,
            vector_top_k=vector_top_k,
            structured_top_k=structured_top_k,
            offset=query.offset,
            limit=limit,
        )
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        self.observability.record_timing(
            "hybrid_search.query.duration_ms",
            duration_ms,
            tags=metric_tags,
        )

        raw_results = raw["results"]
        normalized_items = [self._normalize_result(result) for result in raw_results]
        score_breakdown = self._score_breakdown(normalized_items)
        source_breakdown = self._source_breakdown(raw_results)
        total_before_filters = raw.get("total", len(normalized_items))
        items = normalized_items
        if query.time_range:
            items = self._filter_by_time_range(items, query.time_range)

        # Re-sort by merged score (desc) to ensure deterministic ordering after time filtering
        items.sort(key=lambda item: (item.merged_score is not None, item.merged_score or 0.0), reverse=True)

        diagnostics = self._build_diagnostics(
            raw_payload=raw,
            deduped_count=len(raw_results),
            filtered_count=len(items),
            limit=limit,
            query=query,
            source_breakdown=source_breakdown,
            score_breakdown=score_breakdown,
        )

        vector_hits = int(raw.get("vector_hits", 0) or 0)
        structured_hits = int(raw.get("structured_hits", 0) or 0)
        self.observability.increment(
            "hybrid_search.results.vector_hits",
            value=vector_hits,
            tags=metric_tags,
        )
        self.observability.increment(
            "hybrid_search.results.structured_hits",
            value=structured_hits,
            tags=metric_tags,
        )
        self.observability.increment(
            "hybrid_search.results.returned",
            value=len(items),
            tags=metric_tags,
        )
        self._emit_query_event(
            query=query,
            diagnostics=diagnostics,
            vector_hits=vector_hits,
            structured_hits=structured_hits,
            total=total_before_filters,
            returned=len(items),
            duration_ms=duration_ms,
        )

        return {
            "results": [item.to_dict() for item in items],
            "count": len(items),
            "offset": query.offset,
            "limit": limit,
            "total": total_before_filters,
            "vector_hits": raw.get("vector_hits", 0),
            "structured_hits": raw.get("structured_hits", 0),
            "diagnostics": diagnostics,
        }

    def schema(self) -> Dict[str, Any]:
        """Return the search schema description (cached)."""

        cached = self._schema_from_cache()
        if cached:
            self.observability.increment("hybrid_search.schema.cache_hit")
            return cached.to_dict()

        self.observability.increment("hybrid_search.schema.cache_miss")
        indicator_types = list(self.settings.search.indicator_types)
        datasets = self._resolve_dataset_presets(indicator_types)
        schema = SearchSchema(
            indicator_types=indicator_types,
            datasets=datasets,
            classifications=list(self.settings.search.classification_presets),
            loss_buckets=list(self.settings.search.loss_buckets),
            time_presets=list(self.settings.search.time_presets),
            entity_examples=self._resolve_entity_examples(indicator_types),
        )
        ttl = max(self.settings.search.schema_cache_ttl_seconds, 0)
        if ttl:
            self._schema_cache = (time.time() + ttl, schema)
        return schema.to_dict()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _schema_from_cache(self) -> SearchSchema | None:
        if not self._schema_cache:
            return None
        expires_at, schema = self._schema_cache
        if time.time() < expires_at:
            return schema
        self._schema_cache = None
        return None

    def _build_filter_items(self, query: HybridSearchQuery) -> List[tuple[str, Any]]:
        filters: List[tuple[str, Any]] = []
        for classification in query.classifications:
            filters.append(("classification", classification))
        for dataset in query.datasets:
            filters.append(("dataset", dataset))
        for case_id in query.case_ids:
            filters.append(("case_id", case_id))
        for entity in query.entities:
            filters.append(
                (
                    entity.type,
                    {
                        "filter_type": "entity",
                        "entity_type": entity.type,
                        "value": entity.value,
                        "match_mode": entity.match_mode,
                        "datasets": list(query.datasets),
                        "loss_buckets": list(query.loss_buckets),
                    },
                )
            )
        return filters

    def _normalize_result(self, payload: Dict[str, Any]) -> HybridSearchItem:
        sources = self._ensure_sources(payload.get("sources"))
        vector_score = self._semantic_score(payload.get("vector"))
        structured_score = self._structured_score(payload.get("record"))
        merged_score, scores = self._combine_scores(vector_score, structured_score)
        metadata = self._extract_metadata(payload)
        record = payload.get("record")
        vector = payload.get("vector")
        return HybridSearchItem(
            case_id=str(payload.get("case_id")),
            sources=sources,
            merged_score=merged_score,
            scores=scores,
            record=record,
            vector=vector,
            metadata=metadata,
        )

    @staticmethod
    def _ensure_sources(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, set):
            return sorted(value)
        if isinstance(value, list):
            return value
        return [str(value)]

    @staticmethod
    def _structured_score(record: Dict[str, Any] | None) -> float | None:
        if not record:
            return None
        raw = record.get("confidence")
        if raw is None:
            raw = record.get("metadata", {}).get("score") if isinstance(record.get("metadata"), dict) else None
        if raw is None:
            return 1.0
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 1.0

    @staticmethod
    def _semantic_score(vector_payload: Dict[str, Any] | None) -> float | None:
        if not vector_payload:
            return None
        if "similarity" in vector_payload and vector_payload["similarity"] is not None:
            try:
                return float(vector_payload["similarity"])
            except (TypeError, ValueError):
                return None
        raw = vector_payload.get("score")
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if 0.0 <= value <= 1.0:
            return value
        if value <= 0.0:
            return None
        return 1.0 / (1.0 + value)

    def _combine_scores(self, semantic: float | None, structured: float | None) -> tuple[float | None, Dict[str, Any]]:
        weights = self.settings.search
        scores: Dict[str, Any] = {}
        semantic_weighted: float | None = None
        structured_weighted: float | None = None
        tie_breaker_applied = False
        comparison_delta: float | None = None

        if semantic is not None and weights.semantic_weight > 0:
            scores["semantic"] = semantic
            semantic_weighted = semantic * weights.semantic_weight
            scores["semantic_weighted"] = semantic_weighted

        if structured is not None and weights.structured_weight > 0:
            scores["structured"] = structured
            structured_weighted = structured * weights.structured_weight
            scores["structured_weighted"] = structured_weighted

        winner: str | None = None
        merged_score: float | None = None

        if semantic_weighted is None and structured_weighted is None:
            return None, scores

        if structured_weighted is None:
            winner = "semantic"
            merged_score = semantic_weighted
        elif semantic_weighted is None:
            winner = "structured"
            merged_score = structured_weighted
        else:
            if semantic_weighted is not None and structured_weighted is not None:
                comparison_delta = abs(semantic_weighted - structured_weighted)
            if comparison_delta is not None and comparison_delta <= self._TIE_EPSILON:
                winner = "structured"
                merged_score = structured_weighted
                tie_breaker_applied = True
            elif semantic_weighted > structured_weighted + self._TIE_EPSILON:
                winner = "semantic"
                merged_score = semantic_weighted
            elif structured_weighted > semantic_weighted + self._TIE_EPSILON:
                winner = "structured"
                merged_score = structured_weighted
            else:
                winner = "structured"
                merged_score = structured_weighted
                tie_breaker_applied = True

        scores["winner"] = winner or "unknown"
        if merged_score is not None:
            scores["merged_contribution"] = merged_score
        if comparison_delta is not None:
            scores["comparison_delta"] = comparison_delta
        if tie_breaker_applied:
            scores["tie_breaker_applied"] = True
            scores["tie_breaker"] = self.TIE_BREAKER
        return merged_score, scores

    @staticmethod
    def _extract_metadata(payload: Dict[str, Any]) -> Dict[str, Any] | None:
        record_meta = payload.get("record", {}).get("metadata") if isinstance(payload.get("record"), dict) else None
        vector_meta = payload.get("vector", {}).get("metadata") if isinstance(payload.get("vector"), dict) else None
        metadata: Dict[str, Any] = {}
        if isinstance(record_meta, dict):
            metadata.update(record_meta)
        if isinstance(vector_meta, dict):
            for key, value in vector_meta.items():
                metadata.setdefault(key, value)
        classification = None
        record = payload.get("record")
        if isinstance(record, dict):
            classification = record.get("classification")
        if not classification and isinstance(vector_meta, dict):
            classification = vector_meta.get("classification")
        if classification:
            metadata.setdefault("classification", classification)
        dataset = metadata.get("dataset") or metadata.get("source")
        if dataset:
            metadata["dataset"] = dataset
        return metadata or None

    @staticmethod
    def _filter_by_time_range(items: Sequence[HybridSearchItem], timerange: QueryTimeRange) -> List[HybridSearchItem]:
        start = timerange.start
        end = timerange.end
        filtered: List[HybridSearchItem] = []
        for item in items:
            ts = HybridSearchService._extract_timestamp(item)
            if ts is None or (start <= ts <= end):
                filtered.append(item)
        return filtered

    @staticmethod
    def _extract_timestamp(item: HybridSearchItem) -> datetime | None:
        record = item.record if isinstance(item.record, dict) else None
        if record and record.get("created_at"):
            value = record.get("created_at")
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value)
                except ValueError:
                    return None
        metadata = item.metadata or {}
        value = metadata.get("created_at") or metadata.get("ingested_at")
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None

    def _resolve_dataset_presets(self, indicator_types: Sequence[str]) -> List[str]:
        configured = list(self.settings.search.dataset_presets)
        if not self.entity_store:
            return configured
        dynamic = self.entity_store.list_datasets(entity_types=indicator_types or None)
        return self._merge_preserving_order(configured, dynamic)

    def _resolve_entity_examples(self, indicator_types: Sequence[str]) -> Dict[str, List[str]]:
        if not indicator_types or not self.entity_store:
            return {}
        limit = self.settings.search.schema_entity_example_limit
        raw_examples = self.entity_store.list_entity_examples(
            entity_types=indicator_types,
            datasets=None,
            per_type_limit=limit,
        )
        resolved: Dict[str, List[str]] = {}
        for indicator_type in indicator_types:
            key = indicator_type.strip().lower()
            entries = raw_examples.get(key, [])
            values = [entry.get("value") for entry in entries if entry.get("value")]
            if values:
                resolved[indicator_type] = values
        return resolved

    @staticmethod
    def _merge_preserving_order(primary: Sequence[str], secondary: Sequence[str]) -> List[str]:
        merged: List[str] = []
        seen: set[str] = set()
        for source in (primary, secondary):
            for value in source:
                if value is None:
                    continue
                normalized = value.strip()
                if not normalized:
                    continue
                key = normalized.lower()
                if key in seen:
                    continue
                seen.add(key)
                merged.append(normalized)
        return merged

    def _build_diagnostics(
        self,
        *,
        raw_payload: Dict[str, Any],
        deduped_count: int,
        filtered_count: int,
        limit: int,
        query: HybridSearchQuery,
        source_breakdown: Dict[str, int],
        score_breakdown: Dict[str, Any],
    ) -> Dict[str, Any]:
        vector_hits = int(raw_payload.get("vector_hits", 0) or 0)
        structured_hits = int(raw_payload.get("structured_hits", 0) or 0)
        deduped_overlap = max(vector_hits + structured_hits - deduped_count, 0)
        dropped_by_time = max(deduped_count - filtered_count, 0) if query.time_range else 0

        return {
            "score_policy": {
                "strategy": self.SCORE_STRATEGY,
                "tie_breaker": self.TIE_BREAKER,
                "semantic_weight": self.settings.search.semantic_weight,
                "structured_weight": self.settings.search.structured_weight,
                "winners": score_breakdown.get("winners", {}),
                "tie_breaker_applications": score_breakdown.get("tie_breaker_applied", 0),
            },
            "counts": {
                "vector_hits": vector_hits,
                "structured_hits": structured_hits,
                "merged_results": deduped_count,
                "deduped_overlap": deduped_overlap,
                "returned_results": filtered_count,
                "dropped_by_time_range": dropped_by_time,
                "query_offset": query.offset,
                "query_limit": limit,
                "source_breakdown": source_breakdown,
            },
        }

    def _metric_tags(self, query: HybridSearchQuery) -> Dict[str, str]:
        return {
            "text": "true" if (query.text or "").strip() else "false",
            "entity_filters": "true" if query.entities else "false",
            "classification_filters": str(len(query.classifications)),
            "dataset_filters": str(len(query.datasets)),
            "case_filters": str(len(query.case_ids)),
            "time_filter": "true" if query.time_range else "false",
        }

    def _emit_query_event(
        self,
        *,
        query: HybridSearchQuery,
        diagnostics: Dict[str, Any],
        vector_hits: int,
        structured_hits: int,
        total: int,
        returned: int,
        duration_ms: float,
    ) -> None:
        payload = {
            "duration_ms": round(duration_ms, 3),
            "filters": self._summarize_query(query),
            "counts": {
                "vector_hits": vector_hits,
                "structured_hits": structured_hits,
                "total": total,
                "returned": returned,
                "source_breakdown": diagnostics.get("counts", {}).get("source_breakdown"),
            },
            "score_policy": diagnostics.get("score_policy"),
        }
        self.observability.emit_event("hybrid_search.query", **payload)

    @staticmethod
    def _summarize_query(query: HybridSearchQuery) -> Dict[str, Any]:
        return {
            "has_text": bool((query.text or "").strip()),
            "entity_filters": len(query.entities),
            "classification_filters": len(query.classifications),
            "dataset_filters": len(query.datasets),
            "loss_filters": len(query.loss_buckets),
            "case_filters": len(query.case_ids),
            "time_range": bool(query.time_range),
            "limits": {
                "limit": query.limit,
                "vector_limit": query.vector_limit,
                "structured_limit": query.structured_limit,
                "offset": query.offset,
            },
        }

    @staticmethod
    def _source_breakdown(results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
        """Return counts describing which sources contributed to each merged result."""

        counts = {
            "vector_only": 0,
            "structured_only": 0,
            "text_only": 0,
            "merged": 0,
            "unknown": 0,
            "total": 0,
        }
        for entry in results:
            counts["total"] += 1
            sources = entry.get("sources")
            if isinstance(sources, (list, set, tuple)):
                normalized = {str(source).lower() for source in sources if source is not None}
            elif sources is None:
                normalized = set()
            else:
                normalized = {str(sources).lower()}

            if not normalized:
                counts["unknown"] += 1
                continue

            if len(normalized) > 1:
                counts["merged"] += 1
                continue

            source = next(iter(normalized))
            if source == "vector":
                counts["vector_only"] += 1
            elif source == "structured":
                counts["structured_only"] += 1
            elif source == "text":
                counts["text_only"] += 1
            else:
                counts["unknown"] += 1
        return counts

    @staticmethod
    def _score_breakdown(items: Sequence[HybridSearchItem]) -> Dict[str, Any]:
        """Summarize winner/tie metrics for score policy diagnostics."""

        winners = {"semantic": 0, "structured": 0, "unknown": 0}
        tie_breaks = 0
        for item in items:
            scores = item.scores or {}
            winner = scores.get("winner")
            key = winner if winner in ("semantic", "structured") else "unknown"
            winners[key] += 1
            if scores.get("tie_breaker_applied"):
                tie_breaks += 1
        return {
            "winners": winners,
            "tie_breaker_applied": tie_breaks,
            "evaluated": len(items),
        }
