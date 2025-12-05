# Console Runbook — Search Tab

Use this walkthrough whenever you need to operate the hybrid (semantic + structured) search stack from the Next.js
console, refresh schema metadata, or migrate saved searches.

## Prerequisites
- FastAPI gateway reachable through IAP (`docs/iam.md` lists the enrollment steps).
- `X-API-KEY` with analyst scope exported as `I4G_API_KEY`.
- Convenience shell variables:
  ```bash
  export FASTAPI_BASE=${FASTAPI_BASE:-https://fastapi-gateway-y5jge5w2cq-uc.a.run.app}
  export I4G_API_KEY=${I4G_API_KEY:-dev-analyst-token}
  ```
- Conda environment `i4g` available (`conda run -n i4g ...`).
- Optional: `jq` for formatting responses.

## 1. Refresh the filter schema
1. Fetch the latest taxonomy, datasets, and presets:
   ```bash
   curl -sS \
     -H "X-API-KEY: $I4G_API_KEY" \
     "$FASTAPI_BASE/reviews/search/schema" | jq . > /tmp/hybrid_schema.json
   ```
2. Regenerate the committed snapshot whenever backend metadata changes:
   ```bash
   conda run -n i4g python scripts/refresh_hybrid_schema_snapshot.py \
     --api-base "$FASTAPI_BASE" \
     --api-key "$I4G_API_KEY"
   ```
   The helper rewrites `docs/examples/reviews_search_schema.json`; commit the diff so the UI stays aligned.
3. Inspect the response for:
   - `indicator_types`: entity kinds available to structured search.
   - `datasets`: ingestion labels (for example `retrieval_poc_dev`, `network_smoke`).
   - `loss_buckets`, `time_presets`, and `entity_examples`: power UI defaults and live previews.
4. Share the JSON with UI engineers (drop the payload into `ui/apps/web/src/config/generated/` when schema-driven
   components need a local fixture). The latest reference is committed under `docs/examples/reviews_search_schema.json`.
5. If a dataset or entity is missing, rerun the ingestion smoke in `docs/smoke_test.md#7-network-entities-ingestion-smoke-dev`
   before opening a backend ticket.

## 2. Compose advanced hybrid searches via API
1. Start from the schema output and mirror `HybridSearchRequest` exactly:
   ```bash
   cat <<'EOF' >/tmp/hybrid_query.json
   {
     "text": "romance wallet",
     "classifications": ["romance"],
     "datasets": ["network_smoke"],
     "loss_buckets": [">50k"],
     "entities": [
       {"type": "crypto_wallet", "value": "bc1q", "match_mode": "prefix"},
       {"type": "browser_agent", "value": "chrome", "match_mode": "contains"}
     ],
     "time_range": {
       "start": "2025-11-01T00:00:00Z",
       "end": "2025-12-01T00:00:00Z"
     },
     "limit": 25,
     "vector_limit": 50,
     "structured_limit": 50,
     "offset": 0
   }
   EOF
   ```
2. Execute the search endpoint:
   ```bash
   curl -sS \
     -H "X-API-KEY: $I4G_API_KEY" \
     -H "Content-Type: application/json" \
     -X POST "$FASTAPI_BASE/reviews/search/query" \
     --data @/tmp/hybrid_query.json | jq '{count, total, vector_hits, structured_hits, results: (.results[:3])}'
   ```
3. Confirm the response includes merged `results`, backend hit counters, and `diagnostics.filters_applied`. Capture the
   payload in `planning/change_log.md` whenever you hit anomalies.

## 3. Operate the console Search tab
### Local development
```bash
cd ../ui
pnpm install # once per machine
I4G_API_URL=$FASTAPI_BASE I4G_API_KEY=$I4G_API_KEY pnpm --filter web dev
```
- Browse to http://localhost:3000/search.
- The Advanced Filters drawer hydrates from `/reviews/search/schema`. Restart `pnpm dev` after refreshing the schema file.

### Cloud console
- Visit `https://i4g-console-y5jge5w2cq-uc.a.run.app/search` (IAP-guarded).
- Validate that entity chips, dataset selectors, and saved-search dropdowns mirror the schema payload.
- When schema data changes, refresh the page after running the snapshot helper so chips repopulate.

### Smoke automation
Run the Playwright smoke any time filters or payload contracts shift:
```bash
pnpm --filter web test:smoke
```
The script boots `next dev`, opens `/search`, submits a canned hybrid query, and ensures entity facets render. See
`docs/smoke_test.md` for the extended checklist.

## 4. Saved-search migration playbook
1. Set `[search.saved_search]` defaults in `config/settings.*.toml`. The CLI, Streamlit UI, and Next.js app all read from
   this section, so edit it before exporting or importing searches.
2. Export existing searches per owner or shared scope:
   ```bash
   conda run -n i4g i4g-admin export-saved-searches \
     --owner $USER \
     --limit 100 \
     --schema-version hybrid-v1 \
     --output /tmp/saved_searches_$USER.json
   ```
   Use `--all` to include shared entries. Timestamps are stripped so you can edit safely.
3. Annotate/tag exports with the helper tied to the same defaults:
   ```bash
   conda run -n i4g python scripts/tag_saved_searches.py \
     --input /tmp/saved_searches_$USER.json \
     --output /tmp/saved_searches_${USER}_tagged.json \
     --dedupe
   ```
   `--tag` and `--schema-version` fall back to `[search.saved_search]` unless overridden; `--dedupe` cleans duplicate tags.
4. Import back into SQLite/Firestore:
   ```bash
   conda run -n i4g i4g-admin import-saved-searches \
     --shared \
     --input /tmp/saved_searches_${USER}_tagged.json
   ```
   Drop `--shared` if the searches should remain private.
5. Verify via API and UI:
   ```bash
   curl -sS -H "X-API-KEY: $I4G_API_KEY" "$FASTAPI_BASE/reviews/search/saved" | jq '.items[] | {name, tags, params}'
   ```
   Reload the console and confirm the Saved Search menu lists the imported entries.
6. Cleanup stale entries with `i4g-admin prune-saved-searches --tags legacy --dry-run` before deleting.

## 5. Operational notes
- Missing datasets/entities usually mean ingestion has not replayed. Run the ingestion smoke first, then re-check the
  schema endpoint.
- Schema drift must be accompanied by a refreshed snapshot, a UI restart, and a short entry in `planning/change_log.md`.
- Saved-search conflicts return HTTP 409. Apply a migration tag with `i4g-admin bulk-update-tags --add hybrid-v1` to keep
  ownership clear.
- API requests log `search` actions in `review_actions`; pull the log when performing audits.
- Need dossier guidance? See `docs/runbooks/console/reports.md` for the Reports tab workflow.
