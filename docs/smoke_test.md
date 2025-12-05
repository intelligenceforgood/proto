# Smoke Tests — i4g Prototype

These smoke tests provide fast confidence that core ingestion and review workflows still function after code or infrastructure changes. Keep the shared Conda environment (`i4g`) handy—every command that invokes Python below assumes `conda run -n i4g`.

The scenarios are split into two groups:

- **Local smoke tests** run entirely on your workstation using the embedded SQLite stores.
- **GCP smoke tests** hit the deployed FastAPI gateway and Cloud Run jobs in the dev project (`i4g-dev`).
- **UI smoke tests** boot the Next.js analyst console and verify core filters render (see below).

### Analyst console (Next.js) smoke

Run these from the `ui/` repo root to validate the hybrid-search experience before shipping UI changes:

```bash
pnpm --filter web exec playwright install --with-deps   # first run per machine
pnpm --filter web test:smoke                            # boots next dev + Playwright
```

The suite opens `/search`, confirms the query box, filter sidebar, and primary actions render, and fails fast if the route or API contract regresses. Extend `apps/web/tests/smoke/` with additional assertions when you add new analyst workflows.

## Local Smoke Tests

### Prerequisites

1. Apply the latest migrations so the SQLite store matches the dual-write schema:
  ```bash
  conda run -n i4g python -m alembic upgrade head
  ```
  (Set `I4G_DATABASE_URL` before running the command if you need to target a non-default database.)
2. Populate local demo artifacts if you have a fresh checkout:
   ```bash
   conda run -n i4g python scripts/bootstrap_local_sandbox.py --reset
   ```
3. Launch the FastAPI service in a dedicated terminal. Keep it running while you execute the tests:
   ```bash
   conda run -n i4g uvicorn i4g.api.app:app --host 127.0.0.1 --port 8000
   ```
4. Ensure the analyst token `dev-analyst-token` exists (it ships with the repo). Adjust commands if you use a different key.

### 1. FastAPI Intake Submission + Intake Job (API mode)

1. Prepare a JSON payload and simple attachment for the submission. The example below mirrors what the automated smoke script uses:
   ```bash
   cat <<'EOF' >/tmp/intake_payload.json
   {
     "reporter_name": "Test Reporter",
     "summary": "Suspicious crypto request",
     "details": "Victim asked to send crypto to wallet XYZ.",
     "submitted_by": "qa-smoke",
     "source": "smoke-test",
     "metadata": {
       "environment": "local-smoke",
       "ticket": "SMOKE-INTAKE"
     }
   }
   EOF

   echo "evidence screenshot placeholder" >/tmp/intake_evidence.txt
   ```
2. Submit the intake via the FastAPI endpoint and capture the response:
   ```bash
   curl -s \
     -H "X-API-KEY: dev-analyst-token" \
     -F "payload=$(jq -c . /tmp/intake_payload.json)" \
     -F "files=@/tmp/intake_evidence.txt;type=text/plain" \
     http://127.0.0.1:8000/intakes/ > /tmp/intake_response.json
   ```
3. Extract the identifiers for the job execution:
   ```bash
   export I4G_INTAKE__ID=$(jq -r '.intake_id' /tmp/intake_response.json)
   export I4G_INTAKE__JOB_ID=$(jq -r '.job_id' /tmp/intake_response.json)
   ```
4. Execute the intake worker in API mode so it fetches the record via HTTP. The worker will update status and attach a case ID on success:
   ```bash
   env \
     I4G_INTAKE__ID="$I4G_INTAKE__ID" \
     I4G_INTAKE__JOB_ID="$I4G_INTAKE__JOB_ID" \
     I4G_INTAKE__API_BASE=http://127.0.0.1:8000/intakes \
     I4G_API__KEY=dev-analyst-token \
     conda run -n i4g python -m i4g.worker.jobs.intake
   ```
5. Confirm the job completed:
   ```bash
   curl -s -H "X-API-KEY: dev-analyst-token" \
     "http://127.0.0.1:8000/intakes/$I4G_INTAKE__ID" | jq '{status: .status, job: .job.status, case_id: .case_id}'
   ```
   Expected result:
   ```json
   {
     "status": "processed",
     "job": "completed",
     "case_id": "<matches intake_id>"
   }
   ```

### 2. Ingestion Job Dry Run

Validate that the ingestion worker can read the sample dataset and produce diagnostics without writing to the vector store.

```bash
env \
  I4G_INGEST__JSONL_PATH=$PWD/data/retrieval_poc/cases.jsonl \
  I4G_INGEST__DRY_RUN=true \
  I4G_INGEST__BATCH_LIMIT=3 \
  I4G_RUNTIME__LOG_LEVEL=INFO \
  conda run -n i4g python -m i4g.worker.jobs.ingest
```

Expected log excerpts:
```
Dry run enabled; would ingest case_id=...
Ingestion complete: processed=3 failures=0
```

### 2b. Ingestion Job Dual Write + Vector Check (CLI)

Run two passes of the worker to cover SQL dual writes and the vector store, then assert the tracker row with the
new verification helper.

1. **Dual-write baseline (vector disabled).** Execute the job against the retrieval PoC dataset and capture the
   emitted `run_id`:
   ```bash
   env \
     I4G_INGEST__JSONL_PATH=$PWD/data/retrieval_poc/cases.jsonl \
     I4G_INGEST__DATASET_NAME=retrieval_poc \
     I4G_INGEST__BATCH_LIMIT=5 \
     I4G_INGEST__ENABLE_VECTOR=false \
     I4G_RUNTIME__LOG_LEVEL=INFO \
     conda run -n i4g i4g-ingest-job
   ```
   Expected log highlights:
   ```
   Starting ingestion run b2b67300-9493-4d8e-8418-0928df3b000e (dataset=retrieval_poc)
   Writing SQL case romance_bitcoin-012 ...
   Ingestion run b2b67300-9493-4d8e-8418-0928df3b000e complete: processed=5 vector_writes=0 failures=0
   ```
   When you have Firestore access (emulator or GCP), include `I4G_INGEST__ENABLE_FIRESTORE=true` in the env block.
   The job log will emit `enable_firestore=true` and increment `ingestion_runs.firestore_writes`, confirming the
   Firestore fan-out.
2. **Vector-enabled validation.** Re-run the worker with vectors enabled to ensure embeddings are generated and
   recorded in the tracker row. Use the larger demo dataset so the verification step has enough cases to assert.
   ```bash
   env \
     I4G_INGEST__JSONL_PATH=$PWD/data/retrieval_poc/cases.jsonl \
     I4G_INGEST__DATASET_NAME=cases \
     I4G_INGEST__BATCH_LIMIT=0 \
     I4G_INGEST__ENABLE_VECTOR=true \
     I4G_RUNTIME__LOG_LEVEL=INFO \
     conda run -n i4g i4g-ingest-job
   ```
   Expect `vector_enabled=true` plus `vertex_writes` counts that match the case count.
3. **Automated verification.** Instead of a manual SQLAlchemy snippet, run the helper script to assert the run
   metrics. Target the dataset you just ingested (or pass `--run-id` if you copied it from the logs):
   ```bash
   conda run -n i4g python scripts/verify_ingestion_run.py \
     --dataset cases \
     --min-case-count 50 \
     --require-vector-enabled \
     --max-retry-count 0 \
     --verbose
   ```
   Successful output prints the selected row followed by a summary such as
   `✅ run_id=... dataset=cases status=succeeded cases=50 sql=50 firestore=0 vertex=50 retries=0`. Adjust the flags
   when validating other datasets (for example `--expect-sql-writes 5` or `--status partial`).
4. **Optional Vertex spot check.** Use the admin helper to confirm the dataset landed in Vertex AI:
   ```bash
   conda run -n i4g i4g-admin vertex-search "visa office" \
     --project ${I4G_VERTEX_SEARCH_PROJECT:-i4g-dev} \
     --location ${I4G_VERTEX_SEARCH_LOCATION:-global} \
     --data-store-id ${I4G_VERTEX_SEARCH_DATA_STORE:-retrieval-poc} \
     --page-size 3 \
     --filter 'dataset: ANY("retrieval_poc")'
   ```
   Expect the queries to surface the newly ingested case IDs with `dataset`, `categories`, and `indicator_ids`
   populated.

### 2c. Ingestion Retry Job (CLI)

Use this flow to prove the retry queue fills when a backend fails and that the retry worker drains it cleanly.

1. **Force a Firestore failure to seed the queue.** Re-run the ingestion job with Firestore enabled but point the
   Firestore client at a closed port so writes fail _after_ SQL succeeds. Make sure you temporarily run with the
   `dev` settings profile so Firestore configuration is honoured (local overrides disable it).
   ```bash
   env \
     I4G_ENV=dev \
     I4G_INGEST__JSONL_PATH=$PWD/data/retrieval_poc/cases.jsonl \
     I4G_INGEST__DATASET_NAME=retry_demo \
     I4G_INGEST__BATCH_LIMIT=3 \
     I4G_INGEST__ENABLE_FIRESTORE=true \
     I4G_INGEST__ENABLE_VECTOR=false \
     I4G_STORAGE__FIRESTORE_PROJECT=i4g-dev \
     FIRESTORE_EMULATOR_HOST=127.0.0.1:8787 \
     I4G_RUNTIME__LOG_LEVEL=INFO \
     conda run -n i4g python -m i4g.worker.jobs.ingest
   ```
  Leave the emulator **stopped** for this step—the bogus host triggers connection-refused errors that enqueue
  retries (watch the worker logs for the stack traces). Copy the emitted `run_id`; you will need it for verification.
2. **Inspect the queue without mutating it.**
   ```bash
   env \
     I4G_ENV=dev \
     I4G_STORAGE__FIRESTORE_PROJECT=i4g-dev \
     FIRESTORE_EMULATOR_HOST=127.0.0.1:8787 \
     I4G_INGEST_RETRY__DRY_RUN=true \
     I4G_RUNTIME__LOG_LEVEL=INFO \
     conda run -n i4g python -m i4g.worker.jobs.ingest_retry
   ```
   The dry run prints each pending entry so you can confirm the payloads match the failed Firestore writes.
3. **Execute the retry worker for real.** Start a Firestore emulator (requires Java 21+) or point the job at a real
   Firestore instance so replays succeed. Example emulator workflow:
   ```bash
   # separate terminal
   env JAVA_HOME=/opt/homebrew/opt/openjdk@21 \
       PATH="/opt/homebrew/opt/openjdk@21/bin:$PATH" \
       gcloud beta emulators firestore start --host-port=127.0.0.1:8787 --project i4g-dev
   ```
   Then rerun the retry worker without the dry-run flag:
   ```bash
   env \
     I4G_ENV=dev \
     I4G_STORAGE__FIRESTORE_PROJECT=i4g-dev \
     FIRESTORE_EMULATOR_HOST=127.0.0.1:8787 \
     I4G_RUNTIME__LOG_LEVEL=INFO \
     conda run -n i4g python -m i4g.worker.jobs.ingest_retry
   ```
   - When the queue is empty you should see `No ingestion retry entries ready; exiting`.
   - With queued work the job logs each backend replay, deletes successful entries, and either re-schedules or drops
     failures based on `settings.ingestion.max_retries`. Stop the emulator (`Ctrl+C` or `kill <pid>`) once the run completes.
4. **Validate the ingestion run record.** Use the helper script before and after the replay to confirm the run
   recorded the retries and that the queue drained:
   ```bash
   # Before the replay (expect retries > 0)
   conda run -n i4g python scripts/verify_ingestion_run.py \
     --run-id <run_id_from_step_1> \
     --status succeeded \
     --max-retry-count 3 \
     --verbose

   # After the replay (retry count unchanged but queue empty)
   conda run -n i4g python scripts/verify_ingestion_run.py \
     --run-id <run_id_from_step_1> \
     --status succeeded \
     --max-retry-count 3
   ```
   Adjust the thresholds to match your dataset (for example `--expect-case-count 3`). Re-run the retry worker
   command at the end to confirm it reports `No ingestion retry entries ready`.

### 3. Account List Extraction (Local API + Job)

1. With the FastAPI server from the prerequisites still running, issue an authenticated
   request to the new `/accounts/extract` endpoint. The example below narrows the search window
   and limits results so the run finishes quickly:
   ```bash
   curl -sS -X POST "http://127.0.0.1:8000/accounts/extract" \
     -H "Content-Type: application/json" \
     -H "X-API-KEY: dev-analyst-token" \
     -d '{
       "start_time": "2025-11-01T00:00:00Z",
       "end_time": "2025-11-15T23:59:59Z",
       "top_k": 25,
       "output_formats": ["pdf", "xlsx"]
     }' | jq '{request_id: .request_id, indicators: (.indicators | length), warnings: .warnings}'
   ```
   Expect a JSON payload that lists at least a few indicators plus any warnings for missing
   categories. The artifacts section will contain filesystem paths under `data/reports/account_list`
   when `output_formats` is supplied.
2. Exercise the Cloud Run job entrypoint locally. Start with a dry run to confirm configuration:
   ```bash
   env \
     I4G_ACCOUNT_JOB__WINDOW_DAYS=15 \
     I4G_ACCOUNT_JOB__CATEGORIES=bank,crypto,payments \
     I4G_ACCOUNT_JOB__OUTPUT_FORMATS=pdf,xlsx \
     I4G_ACCOUNT_JOB__DRY_RUN=true \
     I4G_RUNTIME__LOG_LEVEL=INFO \
     conda run -n i4g i4g-account-job
   ```
   Logs should show the computed window plus `Dry run enabled; skipping execution.`
3. Re-run without the dry-run flag so artifacts are generated locally. The command is identical
   minus `I4G_ACCOUNT_JOB__DRY_RUN=true`. Verify that PDFs/XLSX files land in
   `data/reports/account_list/` (or the configured reports bucket / Drive folder when those env vars
   are set) and that the log line `Artifacts generated: {...}` appears.
4. Use the run-history endpoint to confirm the API logged the execution. Both the dedicated
   account-list key and the analyst `X-API-KEY` work thanks to the new fallback:
   ```bash
   curl -s "http://127.0.0.1:8000/accounts/runs?limit=3" \
     -H "X-API-KEY: dev-analyst-token" | jq '{count, latest: .runs[0] | {request_id, indicator_count, artifacts}}'
   ```
   Expect at least one entry whose `request_id` matches the manual run above. The analyst console’s
   `/accounts` page calls the same endpoint to populate its history table.

#### Account list exporter smoke (mock data)

When you only need to confirm that the exporter wiring works (local filesystem, Drive, or Cloud
Storage) without hitting the retriever/LLM stack, run the dedicated mock script:

```bash
conda run -n i4g python tests/adhoc/account_list_export_smoke.py --output-dir data/reports/account_list_smoke
```

The script fabricates a handful of source documents, passes them through stub retriever/extractor
components, and writes CSV/JSON/XLSX/PDF artifacts via `AccountListService`. The output paths are
printed to STDOUT so you can verify Drive/`gs://` uploads or inspect the local files directly.

### 4. Optional Local Checks

- **Streamlit Analyst Dashboard:** Once the FastAPI smoke test succeeds, launch the dashboard (`conda run -n i4g streamlit run src/i4g/ui/analyst_dashboard.py`) and verify that intakes and queue actions render.
- **Vertex Retrieval Smoke:** If you have GCP credentials for Discovery, run `conda run -n i4g python scripts/smoke_vertex_retrieval.py --project <project> --data-store-id <data_store>` to validate the managed search stack. This requires access to the Artifact Registry dataset and may be skipped locally.

Document successful runs (or failures) in `planning/change_log.md` when they drive code or infrastructure updates.

### 5. Dossier Queue Job (Local)

Use this flow to exercise the dossier queue + generator end-to-end on your laptop before enabling the Cloud Run job.

1. **Seed pilot cases and enqueue plans.** The helper writes curated pilot cases into the structured + review stores and
  drops queue entries ready for processing:
  ```bash
  conda run -n i4g i4g-admin pilot-dossiers --case-count 3
  ```
  Expect `✅ Enqueued ... pilot plan(s)` along with any warnings for missing pilot case IDs.
2. **Optional dry run.** Inspect the queue without rendering artifacts by setting the job’s dry-run flag:
  ```bash
  env I4G_DOSSIER__BATCH_SIZE=2 I4G_DOSSIER__DRY_RUN=true I4G_RUNTIME__LOG_LEVEL=INFO \
    conda run -n i4g i4g-dossier-job
  ```
  Logs should report `Dossier queue job complete ... dry_run=True` and leave the queue entries untouched.
3. **Generate dossiers.** Clear the dry-run flag to render markdown + manifest artifacts under
  `data/reports/dossiers/`:
  ```bash
  env I4G_DOSSIER__BATCH_SIZE=2 I4G_RUNTIME__LOG_LEVEL=INFO conda run -n i4g i4g-dossier-job
  ```
  Each processed plan emits `{plan_id}.json`, `{plan_id}.md`, and `{plan_id}.signatures.json` plus chart assets in
  `data/reports/dossiers/assets/`.
4. **Verify queue + artifacts.** Use the CLI or SQLite shell to confirm the queue rows transitioned to `completed`:
  ```bash
  conda run -n i4g i4g-admin process-dossiers --batch-size 3 --dry-run --preview 3
  ls -1 data/reports/dossiers/*.json
  ```
  Inspect a manifest to ensure the signature file path and warnings look reasonable:
  ```bash
  jq '{plan_id, signature: .signature_manifest}' data/reports/dossiers/dossier-*.json | head -n 40
  ```
  Record the run (case count, warnings, hash highlights) in `planning/change_log.md` so Cloud Run parity checks have a
  known baseline.

## GCP Smoke Tests (Dev Environment)

These steps ensure the deployed services, Cloud Run jobs, and shared storage paths cooperate in the dev project. Authenticate with `gcloud auth login` (and `gcloud config set project i4g-dev`) before continuing.

> **Terraform drift warning**
> The `process-intakes` and `account-list` jobs are Terraform-managed. It’s fine to temporarily override their container command/args or env vars with `gcloud run jobs update` while running a smoke test, but you must roll those overrides back afterward or Terraform plans will show persistent diffs. Before changing a job, capture the current command/args so you can restore them:
> ```bash
> gcloud run jobs describe process-intakes \
>   --project i4g-dev --region us-central1 \
>   --format='value(spec.template.spec.template.spec.containers[0].command)'
> ```
> After the test, clear any ad-hoc overrides (command/args/env) or re-run the exact `gcloud run jobs update ...` block from Terraform to put the job back into sync. For example, if you set a custom command while debugging:
> ```bash
> gcloud run jobs update process-intakes \
>   --project i4g-dev --region us-central1 \
>   --clear-command --clear-args
> ```
> Repeat the same pattern for `account-list` (swap the job name) so `terraform plan` stays clean for both services.

### Prerequisites

1. **Update the Cloud Run job environment (one-time per project).** This bakes in the static configuration needed for API mode so you only override the intake identifiers per execution:
   ```bash
   gcloud run jobs update process-intakes \
     --project i4g-dev \
     --region us-central1 \
     --container=container-0 \
     --update-env-vars=I4G_INTAKE__API_BASE=https://fastapi-gateway-y5jge5w2cq-uc.a.run.app/intakes,\
I4G_API__KEY=dev-analyst-token,\
I4G_STORAGE__SQLITE_PATH=/tmp/i4g/sqlite/intake.db,\
I4G_RUNTIME__FALLBACK_DIR=/tmp/i4g,\
I4G_INGEST__ENABLE_VECTOR=false
   ```
   Confirm the change with `gcloud run jobs describe process-intakes --format='value(spec.template.spec.template.spec.containers[0].env)'` if needed.
2. Ensure the remote FastAPI gateway is healthy: https://fastapi-gateway-y5jge5w2cq-uc.a.run.app/ (you should see the default OpenAPI docs).

### 1. Automated Cloud Run Smoke (recommended)

If your workstation already has `gcloud` and `curl` available, run the scripted smoke to submit an intake, execute the Cloud Run job, and verify the processed state in one step:

```bash
conda run -n i4g python scripts/smoke_dev_cloud_run.py
```

The script prints a JSON summary similar to:

```json
{
  "intake_id": "...",
  "job_id": "...",
  "execution": "process-intakes-abcde",
  "intake_status": "processed",
  "job_status": "completed"
}
```

If you prefer to run the workflow manually (useful for debugging individual steps), follow the commands below.

### 2. Submit an Intake via the Deployed API

```bash
curl -sS -L -o /tmp/dev_intake_response.json -w "%{http_code}" \
  -X POST "https://fastapi-gateway-y5jge5w2cq-uc.a.run.app/intakes/" \
  -H "X-API-KEY: dev-analyst-token" \
  -F 'payload={"reporter_name":"Dev Smoke","summary":"Dev smoke submission","details":"Automated smoke test run","source":"smoke-test"}'
```
Expect `201` as the trailing status code. Capture the dynamic identifiers:

```bash
export DEV_INTAKE_ID=$(jq -r '.intake_id' /tmp/dev_intake_response.json)
export DEV_JOB_ID=$(jq -r '.job_id' /tmp/dev_intake_response.json)
```

### 3. Execute the Cloud Run Intake Job

Only the dynamic identifiers change per run because the static configuration is now part of the job definition:

```bash
gcloud run jobs execute process-intakes \
  --project i4g-dev \
  --region us-central1 \
  --wait \
  --container=container-0 \
  --update-env-vars=I4G_INTAKE__ID=$DEV_INTAKE_ID,I4G_INTAKE__JOB_ID=$DEV_JOB_ID
```

You should see `Execution [...] has successfully completed.` in the CLI output.

### 4. Validate the Execution

1. Inspect the Cloud Logging trail for the execution (replace the execution name if different):
   ```bash
   gcloud logging read \
     "resource.type=cloud_run_job AND resource.labels.job_name=process-intakes AND labels.\"run.googleapis.com/execution_name\"=<execution-name>" \
     --project i4g-dev --limit 20 --format text
   ```
   Look for `Intake job completed successfully via API` along with the HTTP 200 updates.
2. Confirm the intake record reflects the processed status:
   ```bash
   curl -sS -H "X-API-KEY: dev-analyst-token" \
     "https://fastapi-gateway-y5jge5w2cq-uc.a.run.app/intakes/$DEV_INTAKE_ID" | \
     jq '{status: .status, job_status: .job.status, case_id: .case_id}'
   ```
   Expected result mirrors the local check (`processed` / `completed`).

Keep a short note in `planning/change_log.md` each time you run the cloud smoke to track regressions or environment drift.

### 5. Account List Cloud Run Job (Dev)

This flow validates the scheduled exporter that now backs the analyst reports.

1. Configure the Cloud Run job environment once so static settings match the dev project. Replace
   the bucket/folder placeholders with real values:
   ```bash
   gcloud run jobs update account-list \
     --project i4g-dev \
     --region us-central1 \
     --container=container-0 \
     --update-env-vars=I4G_RUNTIME__LOG_LEVEL=INFO,\
   I4G_ACCOUNT_JOB__WINDOW_DAYS=15,\
   I4G_ACCOUNT_JOB__CATEGORIES=bank,crypto,payments,\
   I4G_ACCOUNT_JOB__OUTPUT_FORMATS=pdf,xlsx,\
   I4G_ACCOUNT_JOB__INCLUDE_SOURCES=true,\
   I4G_ACCOUNT_LIST__DRIVE_FOLDER_ID=<drive-folder-id>,\
   I4G_STORAGE__REPORTS_BUCKET=<reports-bucket>
   ```
   The nested `I4G_ACCOUNT_LIST__*` and `I4G_STORAGE__*` env vars ensure exporters can publish to
   Google Drive or Cloud Storage the same way they do locally. You can omit Drive settings if the
   reports bucket is sufficient.
2. Execute the job in dry-run mode first to confirm settings parsed correctly:
   ```bash
   gcloud run jobs execute account-list \
     --project i4g-dev \
     --region us-central1 \
     --wait \
     --container=container-0 \
     --update-env-vars=I4G_ACCOUNT_JOB__DRY_RUN=true
   ```
   Look for `Dry run enabled; skipping execution.` inside the execution logs. Remove the dry-run
   override for the actual export:
   ```bash
   gcloud run jobs execute account-list \
     --project i4g-dev \
     --region us-central1 \
     --wait \
     --container=container-0
   ```
3. Inspect Cloud Logging for the execution to confirm counts and artifact uploads:
   ```bash
   gcloud logging read \
     "resource.type=cloud_run_job AND resource.labels.job_name=account-list" \
     --project i4g-dev --limit 50 --format text
   ```
   Expect `Account list run ... completed` plus an `Artifacts generated: {...}` line that references
   Drive links or `gs://` paths.
4. If you publish to Cloud Storage, list the report objects to verify timestamps:
   ```bash
   gsutil ls -r gs://<reports-bucket>/account_list/
   ```
   When Drive uploads are enabled, also spot-check the shared folder for the new files. Record the
   run (success or failure) in `planning/change_log.md` to capture operational history.

### 6. Ingestion Backfill + Retry (Dev)

Use this procedure whenever you need to rehydrate the dual-extraction corpus in `i4g-dev` or
validate that Vertex throttling is handled by the retry worker.

1. **Run the ingestion job with dev overrides.** Execute the worker against the Retrieval PoC bundle
  so SQL, Firestore, and Vertex all receive writes:
  ```bash
  env \
    I4G_ENV=dev \
    I4G_INGEST__JSONL_PATH=$PWD/data/retrieval_poc/cases.jsonl \
    I4G_INGEST__DATASET_NAME=retrieval_poc_dev \
    I4G_INGEST__ENABLE_FIRESTORE=true \
    I4G_STORAGE__FIRESTORE_PROJECT=i4g-dev \
    I4G_VERTEX_SEARCH_PROJECT=i4g-dev \
    I4G_VERTEX_SEARCH_LOCATION=global \
    I4G_VERTEX_SEARCH_DATA_STORE=retrieval-poc \
    I4G_RUNTIME__LOG_LEVEL=INFO \
    conda run -n i4g python -m i4g.worker.jobs.ingest
  ```
  Capture the `run_id` from the logs. Expect SQL/Firestore writes to match the case count (200).
  Vertex imports may stop early if the "Document batch requests/min" quota is exceeded.
2. **Verify the ingestion run.** Use the helper script with relaxed retry thresholds when Vertex
  throttling occurs:
  ```bash
  env I4G_ENV=dev conda run -n i4g python scripts/verify_ingestion_run.py \
    --run-id <run_id> \
    --expect-case-count 200 \
    --status succeeded \
    --max-retry-count 100 \
    --verbose
  ```
  The script prints case/entity counts plus backend write totals so you can snapshot the run
  before draining retries.
3. **Drain queued Firestore/Vertex work.** When Vertex responds with HTTP 429s, run the retry worker
  in small batches until it reports an empty queue:
  ```bash
  env \
    I4G_ENV=dev \
    I4G_STORAGE__FIRESTORE_PROJECT=i4g-dev \
    I4G_VERTEX_SEARCH_PROJECT=i4g-dev \
    I4G_VERTEX_SEARCH_LOCATION=global \
    I4G_VERTEX_SEARCH_DATA_STORE=retrieval-poc \
    I4G_INGEST_RETRY__BATCH_LIMIT=10 \
    I4G_RUNTIME__LOG_LEVEL=INFO \
    conda run -n i4g python -m i4g.worker.jobs.ingest_retry
  ```
  Each pass logs the replayed case IDs plus `successes=/failures=/rescheduled=` totals. Repeat
  until the worker prints `No ingestion retry entries ready; exiting`.
4. **Re-verify and log the run.** Re-run the verification helper (same command as step 2) to record
  the final metrics. `retry_count` remains >0 because the run tracker logs the number of retries
  consumed, but the empty queue confirms Vertex is consistent. Document the run in
  `planning/change_log.md` with the `run_id`, write totals, and any quota notes so future backfills
  have history.
5. **Plan for quota limits.** If Vertex throttling becomes chronic, either lower the ingestion
  batch size (`I4G_INGEST__BATCH_LIMIT`) to stretch writes over time or request a higher
  `Document batch requests per minute` quota before running larger corpora. When tuning these
  values, also adjust `I4G_INGEST_RETRY__BATCH_LIMIT` so the retry worker respects the same rate
  envelope.

### 7. Network Entities Ingestion Smoke (Dev)

Use this flow to mirror the `settings.dev_network_smoke` profile inside Cloud Run without touching
`process-intakes`. The job ingests `data/manual_demo/network_entities.jsonl` into SQL, Firestore, and
Vertex so UI chips stay in sync with the demo dataset.

1. **Create or refresh the dedicated job (one-time per release).** Point the job at the current
   ingest image digest and keep the env vars aligned with
   `config/settings.dev_network_smoke.toml`:
   ```bash
   gcloud run jobs create ingest-network-smoke \
     --project i4g-dev \
     --region us-central1 \
     --image us-central1-docker.pkg.dev/i4g-dev/applications/ingest-job@sha256:f3232cbb5769bdaeb1a706c6aa20b3705e63b5690163d68b460cca5e470cac45 \
     --service-account sa-ingest@i4g-dev.iam.gserviceaccount.com \
     --max-retries 3 \
     --timeout 600s \
     --cpu 1 \
     --memory 512Mi \
     --set-env-vars=I4G_ENV=dev, \
I4G_STORAGE__FIRESTORE_PROJECT=i4g-dev, \
I4G_STORAGE__REPORTS_BUCKET=i4g-reports-dev, \
I4G_VECTOR__BACKEND=vertex_ai, \
I4G_VECTOR__VERTEX_AI__PROJECT=i4g-dev, \
I4G_VECTOR__VERTEX_AI__LOCATION=global, \
I4G_VECTOR__VERTEX_AI__DATA_STORE=retrieval-poc, \
I4G_VECTOR__VERTEX_AI__BRANCH=default_branch, \
I4G_VERTEX_SEARCH_PROJECT=i4g-dev, \
I4G_VERTEX_SEARCH_LOCATION=global, \
I4G_VERTEX_SEARCH_DATA_STORE=retrieval-poc, \
I4G_VERTEX_SEARCH_BRANCH=default_branch, \
I4G_INGEST__JSONL_PATH=/app/data/manual_demo/network_entities.jsonl, \
I4G_INGEST__DEFAULT_DATASET=network_smoke, \
I4G_INGEST__BATCH_LIMIT=1, \
I4G_INGEST__ENABLE_SQL=true, \
I4G_INGEST__ENABLE_VECTOR=true, \
I4G_INGEST__ENABLE_VECTOR_STORE=true, \
I4G_INGEST__ENABLE_VERTEX=true, \
I4G_INGEST__ENABLE_FIRESTORE=true, \
I4G_INGEST__RESET_VECTOR=false, \
I4G_INGEST__DRY_RUN=false, \
I4G_LLM__PROVIDER=mock, \
I4G_RUNTIME__LOG_LEVEL=INFO
   ```
   Re-run the same command with `jobs update` whenever the ingest image or env vars change. Keep the
   digest in sync with the latest `ingest-job` release so Terraform diffs stay predictable.
2. **Execute the smoke job.** Capture the execution name so you can query status and logs without
   retyping:
   ```bash
   EXECUTION=$(gcloud run jobs execute ingest-network-smoke \
     --project i4g-dev \
     --region us-central1 \
     --wait \
     --format='value(metadata.name)')
   echo "Started $EXECUTION"
   ```
   Typical runs finish in under two minutes because the batch size is pinned to `1`.
3. **Inspect the execution status and logs.**
   ```bash
   gcloud run jobs executions describe "$EXECUTION" \
     --project i4g-dev \
     --region us-central1 \
     --format='value(status.conditions)'

   gcloud logging read \
     "resource.type=cloud_run_job AND resource.labels.job_name=ingest-network-smoke AND labels.\"run.googleapis.com/execution_name\"=$EXECUTION" \
     --project i4g-dev --limit 50 --format text
   ```
   Expect the `Completed` condition plus `Ingestion run ... dataset=network_smoke` log lines. If the job
   fails, set `I4G_RUNTIME__LOG_LEVEL=DEBUG` via `jobs update` and rerun before filing an incident.
4. **Verify downstream search + UI.** Use the admin helper to confirm the new cases reached Vertex:
   ```bash
   conda run -n i4g i4g-admin vertex-search "network entity" \
     --project i4g-dev \
     --location global \
     --data-store-id retrieval-poc \
     --filter 'dataset: ANY("network_smoke")'
   ```
   Then run the analyst console smoke from the `ui/` repo (see the "Analyst console" section above) and
   confirm the indicator chips for `network_smoke` cases render. Log the ingest run ID plus any UI
   observations in `planning/change_log.md`.

### 8. Dossier Queue Job (Dev)

Validate the Cloud Run job that consumes dossier queue entries and publishes artifacts to the shared drive / storage.

1. **Seed pilot data in dev.** Point the admin helper at the dev profile so pilot cases and queue entries exist in the
  remote database:
  ```bash
  env I4G_ENV=dev conda run -n i4g i4g-admin pilot-dossiers --case-count 3
  ```
2. **Execute the Cloud Run job.** The Terraform-managed job is named `dossier-queue`. Override the batch size (and
  optionally enable dry run) per execution:
  ```bash
  gcloud run jobs execute dossier-queue \
    --project i4g-dev \
    --region us-central1 \
    --wait \
    --container=container-0 \
    --update-env-vars=I4G_DOSSIER__BATCH_SIZE=2,I4G_DOSSIER__DRY_RUN=false
  ```
  Expect the CLI to print `Execution [...] has successfully completed.`
3. **Review logs and queue status.**
  ```bash
  gcloud logging read \
    "resource.type=cloud_run_job AND resource.labels.job_name=dossier-queue" \
    --project i4g-dev --limit 50 --format text

  curl -sS -H "X-API-KEY: dev-analyst-token" \
    "https://fastapi-gateway-y5jge5w2cq-uc.a.run.app/reports/dossiers?status=completed&limit=5" | \
    jq '{count, plans: [.items[].plan_id]}'
  ```
  Logs should show `Dossier queue job complete` along with the processed/completed counts. The API call confirms plan
  statuses flipped to `completed` and surfaces signature-manifest paths for downstream LEA workflows.
4. **Capture evidence.** Store the execution name, queue status, and any warnings (missing assets, signature mismatches)
  in `planning/change_log.md`. Repeat the smoke whenever you change dossier templates, tool outputs, or queue settings.
