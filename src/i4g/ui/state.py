"""Session state helpers for the analyst dashboard."""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import streamlit as st

from i4g.settings import get_settings

SETTINGS = get_settings()


def ensure_session_defaults() -> None:
    """Populate Streamlit session state with the dashboard defaults."""

    default_api_base = os.getenv("I4G_API__BASE_URL") or os.getenv("I4G_API_URL") or SETTINGS.api.base_url
    default_api_key = os.getenv("I4G_API__KEY") or os.getenv("I4G_API_KEY") or SETTINGS.api.key

    today = date.today()
    default_start = today - timedelta(days=14)

    vertex_project = os.getenv("I4G_VERTEX_SEARCH_PROJECT") or (SETTINGS.vector.vertex_ai_project or "")
    vertex_location = os.getenv("I4G_VERTEX_SEARCH_LOCATION") or (SETTINGS.vector.vertex_ai_location or "global")
    vertex_data_store = os.getenv("I4G_VERTEX_SEARCH_DATA_STORE") or ""
    vertex_serving_config = os.getenv("I4G_VERTEX_SEARCH_SERVING_CONFIG") or "default_search"

    defaults: dict[str, Any] = {
        "api_base": default_api_base,
        "api_key": default_api_key,
        "intake_items": [],
        "intake_error": None,
        "intake_metadata_text": "{}",
        "intake_last_response": None,
        "intake_list_limit": 25,
        "search_results": None,
        "search_error": None,
        "case_reviews": {},
        "search_vector_limit_value": 5,
        "search_structured_limit_value": 5,
        "search_page_size_value": 5,
        "search_params": None,
        "search_offset": 0,
        "search_more_available": False,
        "search_schema": None,
        "search_schema_error": None,
        "search_dataset_filters": [],
        "search_loss_filters": [],
        "search_entity_filters": [],
        "entity_builder_type": "",
        "entity_builder_value": "",
        "entity_builder_match_mode": "exact",
        "search_time_filter_enabled": False,
        "search_time_start": default_start,
        "search_time_end": today,
        "search_time_preset": None,
        "search_history": [],
        "search_history_error": None,
        "history_limit": 10,
        "saved_searches": [],
        "saved_search_error": None,
        "active_saved_search_id": None,
        "tag_filters": set(),
        "saved_tag_filters": [],
        "saved_search_tag_filter": [],
        "bulk_selected_saved_searches": set(),
        "bulk_tags_add": "",
        "bulk_tags_remove": "",
        "bulk_tags_replace": "",
        "preview_enabled": True,
        "vertex_search_project": vertex_project,
        "vertex_search_location": vertex_location,
        "vertex_search_data_store": vertex_data_store,
        "vertex_search_serving_config": vertex_serving_config,
        "vertex_search_page_size": 5,
        "vertex_search_filter": "",
        "vertex_search_boost_json": "",
        "vertex_search_query": "",
        "vertex_search_show_raw": False,
        "vertex_search_results": None,
        "vertex_search_error": None,
        "vertex_search_params": None,
        "account_list_start_date": default_start,
        "account_list_end_date": today,
        "account_list_categories": ["bank", "crypto", "payments"],
        "account_list_top_k": 100,
        "account_list_output_formats": ["pdf", "xlsx"],
        "account_list_include_sources": True,
        "account_list_last_result": None,
        "account_list_error": None,
        "dossier_items": [],
        "dossier_error": None,
        "dossier_status_filter": "completed",
        "dossier_limit": 20,
        "dossier_include_manifest": False,
    }

    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


__all__ = ["ensure_session_defaults"]
