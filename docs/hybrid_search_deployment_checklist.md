# Hybrid Search Deployment Checklist

Use this checklist before promoting hybrid search changes from dev → prod. It mirrors the Milestone 3 delivery scope (filters, saved-search migrations, observability) so backend + UI remain in sync.

## 1. Preflight Configuration
- **Settings file**: ensure `[search]` and `[search.saved_search]` sections match the release (weights, schema cache TTL, migration tag/version). Example override snippet:
  ```toml
  [search]
  semantic_weight = 0.65
  structured_weight = 0.35
  schema_cache_ttl_seconds = 300

  [search.saved_search]
  migration_tag = "hybrid-v1"
  schema_version = "hybrid-v1"
  ```
- **Environment variables** (export or bake into Cloud Run configs):
  | Purpose | Variable |
  | --- | --- |
  | Runtime profile | `I4G_ENV=dev` (or `prod`)
  | Config file | `I4G_SETTINGS_FILE=config/settings.dev.toml` (prod variant accordingly)
  | Review API auth | `I4G_API__KEY`, `I4G_API__BASE_URL`
  | Vertex search | `I4G_VERTEX_SEARCH_PROJECT`, `I4G_VERTEX_SEARCH_LOCATION`, `I4G_VERTEX_SEARCH_DATA_STORE`
  | Observability | `I4G_OBSERVABILITY__SERVICE_NAME`, `I4G_OBSERVABILITY__STATSD_PREFIX`

## 2. Deployment Steps
1. **Cut image / build**: run `pip install -e .`, `pytest tests/unit/services/test_hybrid_search_service.py`, and rebuild the FastAPI + worker images.
2. **Config sync**: run `python scripts/export_settings_manifest.py --docs-repo ../docs` so docs and manifests capture any new knobs.
3. **Apply to dev**:
   - Deploy FastAPI + worker services with the refreshed container image and config overrides.
   - Execute `scripts/bootstrap_local_sandbox.py --reset` if dev data needs the latest entity fields, then run the ingestion job (`i4g-ingest-job`) with the `network_smoke` dataset.
4. **Smoke tests**:
   - API: `curl -sS -H "X-API-KEY: $I4G_API_KEY" "$FASTAPI_BASE/reviews/search/schema"` and confirm indicator/dataset lists include the new fields.
   - Next.js console: `pnpm --filter web test:smoke` (ensures schema-driven chips render).
   - Saved-search tooling: export + tag + import using `i4g-admin export-saved-searches --schema-version hybrid-v1` and `python scripts/tag_saved_searches.py --dedupe`.
5. **Task queue verification**: ensure the in-memory `TASK_STATUS` map (fastapi app logs) reports progress for `/tasks/{id}` responses during hybrid search requests. If not, restart the API after clearing stale state.

## 3. Monitoring & Observability
- **Metrics to watch** (StatsD / OTEL):
  - `hybrid_search.query.total`
  - `hybrid_search.query.duration_ms`
  - `hybrid_search.results.vector_hits`
  - `hybrid_search.results.structured_hits`
  - `hybrid_search.results.returned`
- **Dashboards**: confirm the `hybrid-search` panel in Grafana (or equivalent) charts the counters above with tags for `entity_filters`, `classification_filters`, and `dataset_filters`.
- **Logs**: search for `hybrid_search.query` events; each entry should include `score_policy`, `counts`, and the request filter summary.

## 4. Promotion to Prod
1. Update the prod settings file with the same `[search]` block and saved-search defaults.
2. Re-run ingestion smokes against `i4g-prod` (dataset limited to sanitized bundles) to populate entity examples before flipping the UI feature flag.
3. Execute the same CLI saved-search workflow with prod data to ensure schema versions are embedded before analysts rely on the new filters.
4. After deploy, monitor the metrics + logs for at least one hour; rollback if `hybrid_search.query.duration_ms` spikes above 1.5× the previous baseline.

## 5. Post-Deployment
- Update `planning/change_log.md` with run IDs, dataset names, and any deviations.
- Notify analysts (Slack #analyst-ops) with the schema version, migration tag, and saved-search steps.
- Schedule a follow-up Playwright regression run (`pnpm --filter web test:smoke`) within 24 hours to confirm the UI still consumes live schema payloads.
