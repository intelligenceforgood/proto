"""HTTP and Discovery helpers for the Streamlit analyst dashboard."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Dict, List, Optional
from typing import Sequence as Seq

import httpx
import streamlit as st

try:
    from google.cloud import discoveryengine_v1beta as discoveryengine
    from google.protobuf import json_format
except ImportError:  # pragma: no cover - optional dependency
    discoveryengine = None  # type: ignore[assignment]
    json_format = None  # type: ignore[assignment]

HAS_VERTEX_SEARCH = discoveryengine is not None and json_format is not None


@st.cache_resource
def _search_client() -> Any:
    """Reuse a single Discovery client to avoid reconnect overhead."""

    if not HAS_VERTEX_SEARCH:
        raise RuntimeError(
            "Discovery SDK not installed. Install `google-cloud-discoveryengine` to enable the Vertex search panel."
        )
    return discoveryengine.SearchServiceClient()


from i4g.settings import get_settings

SETTINGS = get_settings()
API_BASE_URL = SETTINGS.api.base_url
API_KEY = SETTINGS.api.key


def _convert_struct(data: Any) -> Any:
    if isinstance(data, (str, int, float, bool)) or data is None:
        return data
    if hasattr(data, "items"):
        return {key: _convert_struct(value) for key, value in data.items()}
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        return [_convert_struct(value) for value in data]
    return data


def perform_vertex_search(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    client = _search_client()

    serving_config = client.serving_config_path(
        project=params["project"],
        location=params["location"],
        data_store=params["data_store_id"],
        serving_config=params.get("serving_config_id", "default_search"),
    )

    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=params["query"],
        page_size=int(params.get("page_size", 5)),
    )

    filter_expression = params.get("filter_expression")
    if filter_expression:
        request.filter = filter_expression

    boost_json = params.get("boost_json")
    if boost_json:
        try:
            boost_data = json.loads(boost_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse BoostSpec JSON: {exc}") from exc

        boost_spec = discoveryengine.SearchRequest.BoostSpec()
        json_format.ParseDict(boost_data, boost_spec._pb)
        request.boost_spec = boost_spec

    try:
        raw_results = list(client.search(request=request))
    except Exception as exc:  # pragma: no cover - network/dependency issues
        raise RuntimeError(f"Vertex search failed: {exc}") from exc

    formatted_results: List[Dict[str, Any]] = []
    for rank, result in enumerate(raw_results, start=1):
        document = result.document
        struct: Dict[str, Any] = {}
        if document.json_data:
            try:
                struct = json.loads(document.json_data)
            except json.JSONDecodeError:
                struct = _convert_struct(document.struct_data) if document.struct_data else {}
        elif document.struct_data:
            struct = _convert_struct(document.struct_data)

        summary = struct.get("summary") or struct.get("text") or struct.get("title") or getattr(document, "title", "")
        tags = struct.get("tags") or []
        label = struct.get("ground_truth_label")

        raw_payload = json_format.MessageToDict(result._pb)  # type: ignore[attr-defined]

        formatted_results.append(
            {
                "rank": rank,
                "document_id": document.id,
                "document_name": document.name,
                "summary": summary,
                "label": label,
                "tags": tags,
                "source": struct.get("source") or struct.get("index_type"),
                "index_type": struct.get("index_type"),
                "struct": struct,
                "rank_signals": raw_payload.get("rankSignals", {}),
                "raw": raw_payload,
            }
        )

    return formatted_results


def vertex_search_available() -> bool:
    """Expose whether the Discovery client dependencies are installed."""

    return HAS_VERTEX_SEARCH


def api_client() -> httpx.Client:
    base = st.session_state.get("api_base", API_BASE_URL)
    key = st.session_state.get("api_key", API_KEY)
    return httpx.Client(base_url=base, headers={"X-API-KEY": key}, timeout=30.0)


def reviews_client() -> httpx.Client:
    base = st.session_state.get("api_base", API_BASE_URL).rstrip("/")
    key = st.session_state.get("api_key", API_KEY)
    reviews_base = f"{base}/reviews"
    return httpx.Client(base_url=reviews_base, headers={"X-API-KEY": key}, timeout=30.0)


def intake_client() -> httpx.Client:
    base = st.session_state.get("api_base", API_BASE_URL).rstrip("/")
    key = st.session_state.get("api_key", API_KEY)
    intake_base = f"{base}/intakes"
    return httpx.Client(base_url=intake_base, headers={"X-API-KEY": key}, timeout=30.0)


def account_list_client() -> httpx.Client:
    base = st.session_state.get("api_base", API_BASE_URL).rstrip("/")
    key = st.session_state.get("api_key", API_KEY)
    accounts_base = f"{base}/accounts"
    headers = {"X-API-KEY": key, "X-ACCOUNTLIST-KEY": key}
    return httpx.Client(base_url=accounts_base, headers=headers, timeout=60.0)


def run_account_list_extraction(payload: Dict[str, Any]) -> Dict[str, Any]:
    client = account_list_client()
    response = client.post("/extract", json=payload)
    response.raise_for_status()
    return response.json()


def fetch_queue(status: str = "queued", limit: int = 50) -> List[Dict[str, Any]]:
    client = reviews_client()
    response = client.get("/queue", params={"status": status, "limit": limit})
    response.raise_for_status()
    data = response.json()
    return data.get("items", [])


def fetch_review(review_id: str) -> Dict[str, Any]:
    client = reviews_client()
    response = client.get(f"/{review_id}")
    response.raise_for_status()
    return response.json()


def post_action(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    client = reviews_client()
    response = client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


def post_patch(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    client = reviews_client()
    response = client.patch(path, json=payload)
    response.raise_for_status()
    return response.json()


def search_cases_api(
    text: Optional[str],
    classification: Optional[str],
    case_id: Optional[str],
    vector_limit: int,
    structured_limit: int,
    page_size: int,
    offset: int,
) -> Dict[str, Any]:
    limit_param = max(vector_limit, structured_limit, page_size)
    params: Dict[str, Any] = {
        "limit": limit_param,
        "offset": offset,
        "page_size": page_size,
        "vector_limit": vector_limit,
        "structured_limit": structured_limit,
    }
    if text:
        params["text"] = text
    if classification:
        params["classification"] = classification
    if case_id:
        params["case_id"] = case_id

    client = reviews_client()
    response = client.get("/search", params=params)
    response.raise_for_status()
    return response.json()


def search_cases_hybrid_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    client = reviews_client()
    response = client.post("/search/query", json=payload)
    response.raise_for_status()
    return response.json()


def fetch_case_reviews(case_id: str, limit: int = 5) -> Dict[str, Any]:
    client = reviews_client()
    response = client.get(f"/case/{case_id}", params={"limit": limit})
    response.raise_for_status()
    return response.json()


def fetch_search_history(limit: int = 10) -> Dict[str, Any]:
    client = reviews_client()
    response = client.get("/search/history", params={"limit": limit})
    response.raise_for_status()
    return response.json()


def fetch_saved_searches(limit: int = 25, owner_only: bool = False) -> Dict[str, Any]:
    client = reviews_client()
    response = client.get("/search/saved", params={"limit": limit, "owner_only": owner_only})
    response.raise_for_status()
    return response.json()


def fetch_search_schema() -> Dict[str, Any]:
    """Retrieve the hybrid-search schema description from the API."""

    client = reviews_client()
    response = client.get("/search/schema")
    response.raise_for_status()
    return response.json()


def save_search(
    name: str,
    params: Dict[str, Any],
    search_id: Optional[str] = None,
    favorite: Optional[bool] = None,
) -> Dict[str, Any]:
    client = reviews_client()
    payload_params = dict(params)
    payload_params.pop("search_id", None)
    body: Dict[str, Any] = {"name": name, "params": payload_params}
    if search_id:
        body["search_id"] = search_id
    if favorite is not None:
        body["favorite"] = favorite
    response = client.post("/search/saved", json=body)
    response.raise_for_status()
    return response.json()


def patch_saved_search(
    search_id: str,
    name: Optional[str] = None,
    tags: Optional[List[str]] = None,
    favorite: Optional[bool] = None,
) -> Dict[str, Any]:
    client = reviews_client()
    payload: Dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if tags is not None:
        payload["tags"] = tags
    if favorite is not None:
        payload["favorite"] = favorite
    response = client.patch(f"/search/saved/{search_id}", json=payload)
    response.raise_for_status()
    return response.json()


def share_saved_search(search_id: str) -> Dict[str, Any]:
    client = reviews_client()
    response = client.post(f"/search/saved/{search_id}/share")
    response.raise_for_status()
    return response.json()


def export_saved_search(search_id: str) -> Dict[str, Any]:
    client = reviews_client()
    response = client.get(f"/search/saved/{search_id}/export")
    response.raise_for_status()
    return response.json()


def import_saved_search_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    client = reviews_client()
    response = client.post("/search/saved/import", json=payload)
    response.raise_for_status()
    return response.json()


def delete_saved_search(search_id: str) -> Dict[str, Any]:
    client = reviews_client()
    response = client.delete(f"/search/saved/{search_id}")
    response.raise_for_status()
    return response.json()


def fetch_tag_presets(limit: int = 100) -> Dict[str, Any]:
    client = reviews_client()
    response = client.get("/search/saved/tag-presets", params={"limit": limit})
    response.raise_for_status()
    return response.json()


def submit_intake(submission: Dict[str, Any], attachments: Seq[tuple[str, bytes, str]]) -> Dict[str, Any]:
    client = intake_client()
    data = {"payload": json.dumps(submission)}
    files = [("files", (name, content, content_type)) for name, content, content_type in attachments]
    response = client.post("/", data=data, files=files if files else None)
    response.raise_for_status()
    return response.json()


def list_intakes(limit: int = 25) -> Dict[str, Any]:
    client = intake_client()
    response = client.get("/", params={"limit": limit})
    response.raise_for_status()
    return response.json()


def fetch_intake(intake_id: str) -> Dict[str, Any]:
    client = intake_client()
    response = client.get(f"/{intake_id}")
    response.raise_for_status()
    return response.json()


def fetch_intake_job(job_id: str) -> Dict[str, Any]:
    client = intake_client()
    response = client.get(f"/jobs/{job_id}")
    response.raise_for_status()
    return response.json()


def _parse_tags(raw_value: str) -> List[str]:
    if not raw_value:
        return []
    return [tag.strip() for tag in raw_value.split(",") if tag.strip()]


def bulk_update_saved_search_tags(
    search_ids: List[str],
    add: Optional[List[str]] = None,
    remove: Optional[List[str]] = None,
    replace: Optional[List[str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"search_ids": search_ids}
    if add:
        payload["add"] = add
    if remove:
        payload["remove"] = remove
    if replace:
        payload["replace"] = replace
    client = reviews_client()
    response = client.post("/search/saved/bulk-tags", json=payload)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = str(exc)
        if exc.response is not None:
            try:
                detail = exc.response.json().get("detail", detail)
            except Exception:
                detail = exc.response.text
        raise RuntimeError(detail) from exc
    return response.json()


__all__ = [
    "perform_vertex_search",
    "api_client",
    "reviews_client",
    "fetch_queue",
    "fetch_review",
    "post_action",
    "post_patch",
    "search_cases_api",
    "fetch_case_reviews",
    "fetch_search_history",
    "fetch_search_schema",
    "fetch_saved_searches",
    "save_search",
    "patch_saved_search",
    "share_saved_search",
    "export_saved_search",
    "import_saved_search_api",
    "delete_saved_search",
    "fetch_tag_presets",
    "_parse_tags",
    "bulk_update_saved_search_tags",
    "intake_client",
    "submit_intake",
    "list_intakes",
    "fetch_intake",
    "fetch_intake_job",
    "account_list_client",
    "run_account_list_extraction",
    "vertex_search_available",
]
