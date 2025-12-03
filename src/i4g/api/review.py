"""Review API router.

Endpoints:
- GET /reviews/queue
- GET /reviews/{review_id}
- POST /reviews/           (enqueue)
- POST /reviews/{review_id}/claim
- POST /reviews/{review_id}/annotate
- POST /reviews/{review_id}/decision
- GET /reviews/{review_id}/actions
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from i4g.api.auth import require_token
from i4g.services.hybrid_search import HybridSearchQuery, HybridSearchService, QueryEntityFilter, QueryTimeRange
from i4g.settings import get_settings
from i4g.store.retriever import HybridRetriever
from i4g.store.review_store import ReviewStore

# Import the worker task — will be scheduled in background on "accepted"
from i4g.worker.tasks import generate_report_for_case

router = APIRouter()
SETTINGS = get_settings()

# Pydantic models for request/response payloads


class EnqueueRequest(BaseModel):
    case_id: str
    priority: Optional[str] = "medium"
    # Optional preview fields for the UI
    text: Optional[str] = None
    classification: Optional[Dict[str, Any]] = None
    entities: Optional[Dict[str, Any]] = None


class DecisionRequest(BaseModel):
    decision: str  # accepted | rejected | needs_more_info
    notes: Optional[str] = None
    auto_generate_report: Optional[bool] = False  # new flag to control auto-generation


class AnnotateRequest(BaseModel):
    annotations: Dict[str, Any]
    notes: Optional[str] = None


class SavedSearchRequest(BaseModel):
    name: str
    params: Dict[str, Any]
    search_id: Optional[str] = None
    favorite: Optional[bool] = False
    tags: Optional[List[str]] = None


class SavedSearchUpdate(BaseModel):
    name: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    favorite: Optional[bool] = None
    tags: Optional[List[str]] = None


class SavedSearchCloneRequest(BaseModel):
    search_id: str


class SavedSearchImportRequest(BaseModel):
    name: str
    params: Dict[str, Any]
    favorite: Optional[bool] = False
    search_id: Optional[str] = None
    tags: Optional[List[str]] = None


class TimeRangeModel(BaseModel):
    start: datetime
    end: datetime


class EntityFilterModel(BaseModel):
    type: str
    value: str
    match_mode: Literal["exact", "prefix", "contains"] = "exact"


class HybridSearchRequest(BaseModel):
    text: Optional[str] = None
    classifications: List[str] = Field(default_factory=list)
    datasets: List[str] = Field(default_factory=list)
    loss_buckets: List[str] = Field(default_factory=list)
    case_ids: List[str] = Field(default_factory=list)
    entities: List[EntityFilterModel] = Field(default_factory=list)
    time_range: Optional[TimeRangeModel] = None
    limit: Optional[int] = Field(default=None, ge=1, le=100)
    vector_limit: Optional[int] = Field(default=None, ge=1, le=100)
    structured_limit: Optional[int] = Field(default=None, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    saved_search_id: Optional[str] = None
    saved_search_name: Optional[str] = None
    saved_search_owner: Optional[str] = None
    saved_search_tags: List[str] = Field(default_factory=list)


class BulkTagUpdateRequest(BaseModel):
    search_ids: List[str]
    add: Optional[List[str]] = None
    remove: Optional[List[str]] = None
    replace: Optional[List[str]] = None


# Dependency factory for store (simple)
def get_store() -> ReviewStore:
    """Return a ReviewStore instance (mounted to default DB path)."""
    return ReviewStore()


def get_retriever() -> HybridRetriever:
    """Return a HybridRetriever instance."""
    return HybridRetriever()


def get_hybrid_search_service() -> HybridSearchService:
    """Return a HybridSearchService instance for dependency injection."""

    return HybridSearchService()


# -----------------------
# Routes
# -----------------------


@router.post("/", summary="Enqueue a case for review")
def enqueue_case(
    payload: EnqueueRequest,
    user=Depends(require_token),
    store: ReviewStore = Depends(get_store),
):
    """Add a case to the review queue."""
    review_id = store.enqueue_case(case_id=payload.case_id, priority=payload.priority)
    # Optionally log that user enqueued it
    store.log_action(
        review_id,
        actor=user["username"],
        action="enqueued",
        payload={"text": payload.text or ""},
    )
    return {"review_id": review_id, "case_id": payload.case_id}


@router.get("/queue", summary="List queued cases")
def list_queue(
    status: str = Query("queued"),
    limit: int = Query(25),
    store: ReviewStore = Depends(get_store),
):
    """List queued cases by status."""
    items = store.get_queue(status=status, limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/search", summary="Search cases across structured/vector stores")
def search_cases(
    text: Optional[str] = Query(None, description="Free-text search for semantic similarity"),
    classification: Optional[str] = Query(None, description="Filter by classification label"),
    case_id: Optional[str] = Query(None, description="Filter by exact case ID"),
    limit: int = Query(5, ge=1, le=50),
    vector_limit: Optional[int] = Query(None, ge=1, le=50),
    structured_limit: Optional[int] = Query(None, ge=1, le=50),
    offset: int = Query(0, ge=0),
    page_size: Optional[int] = Query(None, ge=1, le=100, description="Maximum number of merged results to return"),
    search_service: HybridSearchService = Depends(get_hybrid_search_service),
    user=Depends(require_token),
    store: ReviewStore = Depends(get_store),
):
    """Combine semantic and structured search for analyst triage."""

    payload = HybridSearchRequest(
        text=text,
        classifications=[classification] if classification else [],
        case_ids=[case_id] if case_id else [],
        limit=page_size or limit,
        vector_limit=vector_limit,
        structured_limit=structured_limit,
        offset=offset,
    )
    query = _build_hybrid_query_from_request(payload)
    query_result = search_service.search(query)
    results = query_result["results"]
    diagnostics = query_result.get("diagnostics")
    diag_counts = diagnostics.get("counts", {}) if isinstance(diagnostics, dict) else {}
    search_id = f"search:{uuid.uuid4()}"
    store.log_action(
        review_id="search",
        actor=user["username"],
        action="search",
        payload={
            "search_id": search_id,
            "text": text,
            "classification": classification,
            "case_id": case_id,
            "limit": limit,
            "vector_limit": vector_limit,
            "structured_limit": structured_limit,
            "offset": offset,
            "page_size": page_size,
            "results_count": len(results),
            "total": query_result["total"],
            "vector_hits": query_result.get("vector_hits"),
            "structured_hits": query_result.get("structured_hits"),
            "merged_results": diag_counts.get("merged_results"),
            "source_breakdown": diag_counts.get("source_breakdown"),
            "diagnostics": diagnostics,
        },
    )

    return {
        "results": results,
        "count": len(results),
        "offset": offset,
        "limit": page_size or len(results),
        "total": query_result["total"],
        "vector_hits": query_result.get("vector_hits"),
        "structured_hits": query_result.get("structured_hits"),
        "merged_results": diag_counts.get("merged_results"),
        "source_breakdown": diag_counts.get("source_breakdown"),
        "diagnostics": diagnostics,
        "search_id": search_id,
    }


@router.get("/search/history", summary="List recent search actions")
def search_history(
    limit: int = Query(20, ge=1, le=200),
    store: ReviewStore = Depends(get_store),
    user=Depends(require_token),
):
    """Return recent search audit entries."""
    actions = store.get_recent_actions(action="search", limit=limit)
    return {"events": actions, "count": len(actions)}


@router.post("/search/query", summary="Execute advanced hybrid search with structured filters")
def search_cases_advanced(
    payload: HybridSearchRequest,
    search_service: HybridSearchService = Depends(get_hybrid_search_service),
    user=Depends(require_token),
    store: ReviewStore = Depends(get_store),
):
    query = _build_hybrid_query_from_request(payload)
    query_result = search_service.search(query)
    search_id = f"search:{uuid.uuid4()}"
    diagnostics = query_result.get("diagnostics")
    diag_counts = diagnostics.get("counts", {}) if isinstance(diagnostics, dict) else {}
    saved_search_descriptor = _build_saved_search_descriptor(payload)
    log_payload: Dict[str, Any] = {
        "search_id": search_id,
        "request": payload.model_dump(),
        "results_count": query_result["count"],
        "total": query_result["total"],
        "vector_hits": query_result.get("vector_hits"),
        "structured_hits": query_result.get("structured_hits"),
        "merged_results": diag_counts.get("merged_results"),
        "source_breakdown": diag_counts.get("source_breakdown"),
        "diagnostics": diagnostics,
    }
    if saved_search_descriptor:
        log_payload["saved_search"] = saved_search_descriptor
        if saved_search_descriptor.get("id"):
            log_payload["saved_search_id"] = saved_search_descriptor["id"]
        if saved_search_descriptor.get("name"):
            log_payload["saved_search_name"] = saved_search_descriptor["name"]
        if saved_search_descriptor.get("owner"):
            log_payload["saved_search_owner"] = saved_search_descriptor["owner"]
        if saved_search_descriptor.get("tags"):
            log_payload["saved_search_tags"] = saved_search_descriptor["tags"]
    store.log_action(
        review_id="search",
        actor=user["username"],
        action="search",
        payload=log_payload,
    )
    return {**query_result, "search_id": search_id}


@router.get("/search/schema", summary="Describe hybrid search filters for clients")
def get_search_schema(
    search_service: HybridSearchService = Depends(get_hybrid_search_service),
    user=Depends(require_token),
):
    return search_service.schema()


@router.post("/search/saved", summary="Create or update a saved search")
def save_search(
    payload: SavedSearchRequest,
    store: ReviewStore = Depends(get_store),
    user=Depends(require_token),
):
    params = _normalize_saved_search_params(payload.params)
    try:
        search_id = store.upsert_saved_search(
            payload.name,
            params,
            owner=user.get("username"),
            search_id=payload.search_id,
            favorite=payload.favorite or False,
            tags=payload.tags or [],
        )
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("duplicate_saved_search"):
            owner = "shared"
            if ":" in msg:
                owner_val = msg.split(":", 1)[1]
                owner = owner_val or "shared"
            raise HTTPException(
                status_code=409,
                detail=f"Saved search name already exists (owner={owner})",
            )
        raise
    return {"search_id": search_id}


@router.get("/search/saved", summary="List saved searches")
def list_saved_searches(
    limit: int = Query(50, ge=1, le=200),
    owner_only: bool = Query(False, description="If true, only return searches owned by the caller"),
    store: ReviewStore = Depends(get_store),
    user=Depends(require_token),
):
    owner = user.get("username") if owner_only else None
    raw_items = store.list_saved_searches(owner=owner, limit=limit)
    items = []
    for entry in raw_items:
        params = entry.get("params") if isinstance(entry, dict) else None
        if isinstance(entry, dict):
            normalized = dict(entry)
            normalized["params"] = _normalize_saved_search_params(params or {}, strict=False)
            items.append(normalized)
        else:
            items.append(entry)
    return {"items": items, "count": len(items)}


@router.get("/search/tag-presets", summary="List tag presets derived from saved searches")
def list_tag_presets(
    limit: int = Query(50, ge=1, le=200),
    owner_only: bool = Query(False, description="If true, only return tag presets owned by the caller"),
    include_shared: bool = Query(True, description="Include shared presets when listing"),
    store: ReviewStore = Depends(get_store),
    user=Depends(require_token),
):
    owner = user.get("username") if owner_only else None
    effective_owner = None if (include_shared and not owner_only) else owner
    presets = store.list_tag_presets(owner=effective_owner, limit=limit)
    return {"presets": presets, "count": len(presets)}


@router.post("/search/saved/bulk-tags", summary="Bulk update tags for saved searches")
def bulk_update_tags(
    payload: BulkTagUpdateRequest,
    store: ReviewStore = Depends(get_store),
    user=Depends(require_token),
):
    if not payload.search_ids:
        raise HTTPException(status_code=400, detail="No search IDs provided")
    updated = store.bulk_update_tags(
        payload.search_ids,
        add=payload.add,
        remove=payload.remove,
        replace=payload.replace,
    )
    return {"updated": updated}


@router.delete("/search/saved/{search_id}", summary="Delete a saved search")
def delete_saved_search(
    search_id: str,
    store: ReviewStore = Depends(get_store),
    user=Depends(require_token),
):
    deleted = store.delete_saved_search(search_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Saved search not found")
    return {"deleted": True, "search_id": search_id}


@router.patch("/search/saved/{search_id}", summary="Update a saved search")
def patch_saved_search(
    search_id: str,
    payload: SavedSearchUpdate,
    store: ReviewStore = Depends(get_store),
    user=Depends(require_token),
):
    params = _normalize_saved_search_params(payload.params) if payload.params is not None else None
    try:
        updated = store.update_saved_search(
            search_id,
            name=payload.name,
            params=params,
            favorite=payload.favorite,
            tags=payload.tags,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("duplicate_saved_search"):
            owner = "shared"
            if ":" in msg:
                owner_val = msg.split(":", 1)[1]
                owner = owner_val or "shared"
            raise HTTPException(
                status_code=409,
                detail=f"Saved search name already exists (owner={owner})",
            )
        raise
    if not updated:
        raise HTTPException(status_code=404, detail="Saved search not found or nothing to update")
    return {"updated": True, "search_id": search_id}


@router.post("/search/saved/{search_id}/share", summary="Promote a saved search to shared scope")
def share_saved_search(
    search_id: str,
    store: ReviewStore = Depends(get_store),
    user=Depends(require_token),
):
    try:
        shared_id = store.clone_saved_search(search_id, target_owner=None)
    except ValueError as exc:
        msg = str(exc)
        if msg == "saved_search_not_found":
            raise HTTPException(status_code=404, detail="Saved search not found")
        if msg.startswith("duplicate_saved_search"):
            owner = "shared"
            if ":" in msg:
                owner_val = msg.split(":", 1)[1]
                owner = owner_val or "shared"
            raise HTTPException(
                status_code=409,
                detail=f"Shared search name already exists (owner={owner})",
            )
        raise
    return {"search_id": shared_id}


@router.get("/search/saved/{search_id}/export", summary="Export a saved search configuration")
def export_saved_search(
    search_id: str,
    store: ReviewStore = Depends(get_store),
    user=Depends(require_token),
):
    record = store.get_saved_search(search_id)
    if not record:
        raise HTTPException(status_code=404, detail="Saved search not found")
    record["params"] = _normalize_saved_search_params(record.get("params") or {}, strict=False)
    return record


@router.post("/search/saved/import", summary="Import a saved search definition")
def import_saved_search(
    payload: SavedSearchImportRequest,
    store: ReviewStore = Depends(get_store),
    user=Depends(require_token),
):
    record = payload.model_dump()
    record["params"] = _normalize_saved_search_params(record["params"])
    try:
        search_id = store.import_saved_search(record, owner=user.get("username"))
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("duplicate_saved_search"):
            owner = "shared"
            if ":" in msg:
                owner_val = msg.split(":", 1)[1]
                owner = owner_val or "shared"
            raise HTTPException(
                status_code=409,
                detail=f"Saved search name already exists (owner={owner})",
            )
        raise HTTPException(status_code=400, detail="Invalid saved search payload")
    return {"search_id": search_id}


@router.get("/case/{case_id}", summary="List review entries for a given case")
def reviews_by_case(
    case_id: str,
    limit: int = Query(5, ge=1, le=50),
    store: ReviewStore = Depends(get_store),
    user=Depends(require_token),
):
    """Return review queue entries associated with a specific case."""
    reviews = store.get_reviews_by_case(case_id=case_id, limit=limit)
    return {"case_id": case_id, "reviews": reviews, "count": len(reviews)}


@router.get("/{review_id}", summary="Get a review item")
def get_review(review_id: str, store: ReviewStore = Depends(get_store)):
    """Get full review item by ID."""
    item = store.get_review(review_id)
    if not item:
        raise HTTPException(status_code=404, detail="Review not found")
    return item


def _build_hybrid_query_from_request(payload: HybridSearchRequest) -> HybridSearchQuery:
    """Convert API payload into the service query dataclass."""

    entities = [
        QueryEntityFilter(type=entity.type, value=entity.value, match_mode=entity.match_mode)
        for entity in payload.entities
    ]
    time_range = None
    if payload.time_range:
        if payload.time_range.end < payload.time_range.start:
            raise HTTPException(status_code=400, detail="time_range.end must be after start")
        time_range = QueryTimeRange(start=payload.time_range.start, end=payload.time_range.end)

    return HybridSearchQuery(
        text=payload.text,
        classifications=payload.classifications,
        datasets=payload.datasets,
        loss_buckets=payload.loss_buckets,
        case_ids=payload.case_ids,
        entities=entities,
        time_range=time_range,
        limit=payload.limit,
        vector_limit=payload.vector_limit,
        structured_limit=payload.structured_limit,
        offset=payload.offset,
    )


def _build_saved_search_descriptor(payload: HybridSearchRequest) -> Dict[str, Any] | None:
    tags: List[str] = []
    for tag in payload.saved_search_tags or []:
        text = _clean_text_value(tag)
        if text:
            tags.append(text)

    descriptor: Dict[str, Any] = {
        "id": _clean_text_value(payload.saved_search_id),
        "name": _clean_text_value(payload.saved_search_name),
        "owner": _clean_text_value(payload.saved_search_owner),
        "tags": tags,
    }

    if descriptor["id"] or descriptor["name"] or descriptor["owner"] or descriptor["tags"]:
        return descriptor
    return None


def _normalize_saved_search_params(params: Dict[str, Any], *, strict: bool = True) -> Dict[str, Any]:
    if not isinstance(params, dict):
        if strict:
            raise HTTPException(status_code=400, detail="Saved search params must be an object")
        return _apply_saved_search_schema_version({})

    try:
        request_model = _build_saved_search_request(params)
    except HTTPException:
        if strict:
            raise
        return _apply_saved_search_schema_version(dict(params))
    except ValidationError as exc:  # Safety net when coercion fails downstream
        if strict:
            raise HTTPException(status_code=400, detail=f"Invalid saved search params: {exc.errors()[0]['msg']}")
        return _apply_saved_search_schema_version(dict(params))

    normalized = request_model.model_dump(exclude_none=True)
    normalized = _post_process_saved_search_params(normalized, params)
    normalized = _apply_saved_search_schema_version(normalized, provided=params.get("schema_version"))
    return normalized


def _saved_search_schema_version_default() -> str | None:
    configured = (SETTINGS.search.saved_search.schema_version or "").strip()
    if configured:
        return configured
    fallback = (SETTINGS.search.saved_search.migration_tag or "").strip()
    return fallback or None


def _build_saved_search_request(params: Dict[str, Any]) -> HybridSearchRequest:
    payload: Dict[str, Any] = {}

    payload["text"] = _clean_text_value(params.get("text"))
    payload["classifications"] = _coerce_string_list(params.get("classifications"), params.get("classification"))
    payload["datasets"] = _coerce_string_list(params.get("datasets"))
    payload["loss_buckets"] = _coerce_string_list(params.get("loss_buckets"))
    payload["case_ids"] = _coerce_string_list(params.get("case_ids"), params.get("case_id"))
    payload["entities"] = _coerce_entities(params.get("entities"))

    time_range = _coerce_time_range(params.get("time_range"))
    if time_range:
        payload["time_range"] = time_range

    limit = _coerce_positive_int(params.get("limit"), max_value=100)
    if not limit:
        limit = _coerce_positive_int(params.get("page_size"), max_value=100)
    if not limit:
        limit = min(SETTINGS.search.default_limit, 100)
    payload["limit"] = limit
    payload["vector_limit"] = _coerce_positive_int(params.get("vector_limit"), max_value=100) or limit
    payload["structured_limit"] = _coerce_positive_int(params.get("structured_limit"), max_value=100) or limit
    payload["offset"] = _coerce_positive_int(params.get("offset"), allow_zero=True, max_value=10_000) or 0

    return HybridSearchRequest(**payload)


def _post_process_saved_search_params(normalized: Dict[str, Any], original: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(normalized)

    # Preserve legacy scalar fields for older clients
    classification_value = _first_value(result.get("classifications"), original.get("classification"))
    if classification_value:
        result["classification"] = classification_value

    case_value = _first_value(result.get("case_ids"), original.get("case_id"))
    if case_value:
        result["case_id"] = case_value

    # Align limit/page size defaults
    provided_page_size = _coerce_positive_int(original.get("page_size"), max_value=100)
    if provided_page_size:
        result["page_size"] = provided_page_size
        result.setdefault("limit", provided_page_size)
    else:
        result.setdefault("page_size", result.get("limit"))

    result["vector_limit"] = result.get("vector_limit") or result.get("limit")
    result["structured_limit"] = result.get("structured_limit") or result.get("limit")

    # Ensure lists exist for downstream UI expectations
    for field in ("classifications", "datasets", "loss_buckets", "case_ids", "entities"):
        result[field] = result.get(field) or []

    if result.get("time_range"):
        tr = result["time_range"]
        result["time_range"] = {
            "start": tr["start"].isoformat() if isinstance(tr["start"], datetime) else tr["start"],
            "end": tr["end"].isoformat() if isinstance(tr["end"], datetime) else tr["end"],
        }

    return result


def _apply_saved_search_schema_version(params: Dict[str, Any], provided: Any | None = None) -> Dict[str, Any]:
    normalized = dict(params)
    candidates = []
    if provided is not None:
        candidates.append(provided)
    if "schema_version" in normalized:
        candidates.append(normalized["schema_version"])
    version_value = ""
    for candidate in candidates:
        if isinstance(candidate, str):
            version_value = candidate.strip()
        elif candidate is not None:
            version_value = str(candidate).strip()
        if version_value:
            break

    if version_value:
        normalized["schema_version"] = version_value
        return normalized

    fallback = _saved_search_schema_version_default()
    if fallback:
        normalized["schema_version"] = fallback
    else:
        normalized.pop("schema_version", None)
    return normalized


def _coerce_string_list(*values: Any) -> List[str]:
    result: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            for item in value:
                text = _clean_text_value(item)
                if text:
                    result.append(text)
        else:
            text = _clean_text_value(value)
            if text:
                result.append(text)
    # Remove duplicates while preserving order
    seen = set()
    unique: List[str] = []
    for item in result:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(item)
    return unique


def _coerce_entities(raw: Any) -> List[Dict[str, str]]:
    if not raw:
        return []
    normalized: List[Dict[str, str]] = []
    match_modes = {"exact", "prefix", "contains"}
    candidates = raw if isinstance(raw, list) else [raw]
    for entry in candidates:
        if isinstance(entry, dict):
            entity_type = _clean_text_value(entry.get("type"))
            entity_value = _clean_text_value(entry.get("value"))
            if not entity_type or not entity_value:
                continue
            match_mode = _clean_text_value(entry.get("match_mode")) or "exact"
            if match_mode not in match_modes:
                match_mode = "exact"
            normalized.append({"type": entity_type, "value": entity_value, "match_mode": match_mode})
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            entity_type = _clean_text_value(entry[0])
            entity_value = _clean_text_value(entry[1])
            if not entity_type or not entity_value:
                continue
            normalized.append({"type": entity_type, "value": entity_value, "match_mode": "exact"})
    return normalized


def _coerce_time_range(raw: Any) -> Dict[str, datetime] | None:
    if not isinstance(raw, dict):
        return None
    start_value = raw.get("start") or raw.get("from")
    end_value = raw.get("end") or raw.get("to")
    if not start_value or not end_value:
        return None
    start_dt = _parse_datetime(start_value)
    end_dt = _parse_datetime(end_value)
    if not start_dt or not end_dt or end_dt < start_dt:
        return None
    return {"start": start_dt, "end": end_dt}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _coerce_positive_int(value: Any, *, allow_zero: bool = False, max_value: int | None = None) -> Optional[int]:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or (number == 0 and not allow_zero):
        return None
    if max_value is not None and number > max_value:
        return max_value
    return number


def _first_value(*candidates: Any) -> Optional[str]:
    for candidate in candidates:
        text = _clean_text_value(candidate)
        if text:
            return text
    return None


def _clean_text_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


@router.post("/{review_id}/claim", summary="Claim a review")
def claim_review(review_id: str, user=Depends(require_token), store: ReviewStore = Depends(get_store)):
    """Assign current user to the review and log action."""
    store.update_status(review_id, status="in_review", notes=f"claimed by {user['username']}")
    store.log_action(review_id, actor=user["username"], action="claimed")
    return {"review_id": review_id, "status": "in_review"}


@router.post("/{review_id}/annotate", summary="Annotate a review item")
def annotate_review(
    review_id: str,
    payload: AnnotateRequest,
    user=Depends(require_token),
    store: ReviewStore = Depends(get_store),
):
    """Attach annotations and notes to a review; logs action."""
    # Save annotation into actions for now
    store.log_action(
        review_id,
        actor=user["username"],
        action="annotate",
        payload={"annotations": payload.annotations, "notes": payload.notes},
    )
    return {"review_id": review_id, "annotated": True}


@router.post("/{review_id}/decision", summary="Make a decision on a review")
def decision(
    review_id: str,
    payload: DecisionRequest,
    background_tasks: BackgroundTasks,
    user=Depends(require_token),
    store: ReviewStore = Depends(get_store),
):
    """Record a decision (accepted/rejected/needs_more_info).

    If decision is 'accepted' and auto_generate_report is True, schedule background report generation.
    """
    if payload.decision not in {"accepted", "rejected", "needs_more_info", "in_review"}:
        raise HTTPException(status_code=400, detail="Invalid decision")

    store.update_status(review_id, status=payload.decision, notes=payload.notes)
    store.log_action(
        review_id,
        actor=user["username"],
        action="decision",
        payload={"decision": payload.decision, "notes": payload.notes},
    )

    # If accepted and auto_generate_report is requested, schedule background job
    if payload.decision == "accepted" and payload.auto_generate_report:
        # Schedule background task to generate and export report
        # generate_report_for_case will use the default ReviewStore and exporter,
        # and will log action results back into the store.
        background_tasks.add_task(generate_report_for_case, review_id, store)

    return {"review_id": review_id, "status": payload.decision}


@router.get("/{review_id}/actions", summary="Get review action history")
def actions(review_id: str, store: ReviewStore = Depends(get_store)):
    """Return audit trail for a review."""
    actions = store.get_actions(review_id)
    return {"review_id": review_id, "actions": actions}
