# Dossier Deployment & Observability Checklist

Follow this checklist whenever you roll out dossier-pipeline changes (templates, LangChain tools, queue processor, or
Cloud Run job configuration). The checklist keeps smoke coverage, environment variables, and telemetry expectations in
sync across dev and prod.

## 1. Pre-flight validation
- **Run unit tests:** `conda run -n i4g pytest tests/unit/reports/test_dossier_*`.
- **Regenerate golden samples:** Execute `conda run -n i4g pytest tests/unit/reports/test_dossier_golden_regression.py`
  and confirm only intended hash deltas appear.
- **Local job dry run:**
  ```bash
  conda run -n i4g i4g-admin pilot-dossiers --case-count 3
  env I4G_DOSSIER__BATCH_SIZE=3 I4G_DOSSIER__DRY_RUN=true conda run -n i4g i4g-dossier-job
  ```
- **Local job execution:** Clear `I4G_DOSSIER__DRY_RUN` and rerun to emit real manifests + markdown. Inspect
  `data/reports/dossiers/*.json` for warnings and ensure signature manifests reference the markdown + chart artifacts.
- **Console smoke:** Load `/reports/dossiers`, verify the new bundles appear, and run inline signature verification on at
  least one plan.
- Log the run (plan IDs, warnings, hash fragments) in `planning/change_log.md`.

## 2. Configuration matrix
| Setting | Scope | Purpose | Example |
| --- | --- | --- | --- |
| `I4G_REPORT__DRIVE_PARENT_ID` | FastAPI & Cloud Run | Google Drive folder for published dossiers | `1O4k...shared` |
| `I4G_REPORT__MIN_LOSS_USD` | BundleBuilder | Reject cases below the loss floor | `50000` |
| `I4G_REPORT__RECENCY_DAYS` | BundleBuilder | Rolling acceptance window | `30` |
| `I4G_REPORT__HASH_ALGORITHM` | Generator + signature manifest | Hash algorithm for manifests/assets | `sha256` |
| `I4G_DOSSIER__BATCH_SIZE` | Queue job | Plans processed per execution | `3` |
| `I4G_DOSSIER__DRY_RUN` | Queue job | Inspect queue without writing artifacts | `false` |
| `I4G_RUNTIME__LOG_LEVEL` | Queue job | Cloud Run logging verbosity | `INFO` |
| `I4G_STORAGE__SQLITE_PATH` (dev) | Queue job | Path to SQLite queue/db when not using Cloud SQL | `/tmp/i4g/sqlite/review.db` |
| `I4G_ENV` | All services | Selects settings profile (`local`, `dev`, `prod`) | `dev` |

Keep the `report.*` defaults in `config/settings*.toml` aligned with the table above and mirror overrides in the env var
manifest stored in `docs/config/`.

## 3. Update the Cloud Run job
1. Publish the new container image:
   ```bash
   docker buildx build \
     --platform linux/amd64 \
     -f docker/dossier-job.Dockerfile \
     -t us-central1-docker.pkg.dev/i4g-dev/applications/dossier-job:dev \
     --push .
   ```
   For prod, push the `:prod` tag in the prod project.
2. Update the Terraform variables (`infra/environments/{dev,prod}/terraform.tfvars`) if you change the tag.
3. Force Cloud Run to pull the new digest and apply env overrides:
   ```bash
   gcloud run jobs update dossier-queue \
     --project i4g-dev \
     --region us-central1 \
     --image us-central1-docker.pkg.dev/i4g-dev/applications/dossier-job:dev \
     --container=container-0 \
     --update-env-vars=I4G_ENV=dev,I4G_REPORT__HASH_ALGORITHM=sha256,\
I4G_REPORT__DRIVE_PARENT_ID=$DRIVE_PARENT,\
I4G_DOSSIER__BATCH_SIZE=3,\
I4G_DOSSIER__DRY_RUN=false,\
I4G_RUNTIME__LOG_LEVEL=INFO
   ```
   Mirrors the Terraform configuration but lets you test before committing infra changes.
4. Execute the job with a controlled batch size:
   ```bash
   EXECUTION=$(gcloud run jobs execute dossier-queue \
     --project i4g-dev \
     --region us-central1 \
     --wait \
     --format='value(metadata.name)')
   echo "Execution $EXECUTION completed"
   ```
5. Restore Terraform parity (`terraform apply`) once the job finishes successfully.

## 4. Observability and verification
- **Task_STATUS:** Visit `/tasks/{execution_id}` (or watch the Streamlit Task_STATUS widget) to confirm the job emitted
  `started` and `finished` events with processed/completed counts.
- **Cloud Logging:**
  ```bash
  gcloud logging read \
    "resource.type=cloud_run_job AND resource.labels.job_name=dossier-queue" \
    --project i4g-dev --limit 50 --format text
  ```
  Look for `Dossier queue job complete` plus warning summaries.
- **Metrics:** Ensure StatsD/OTel collectors receive `dossier.generated`, `dossier.failed`, and `bundle.enqueue` deltas.
  Dev environments typically forward to the local StatsD sidecar; prod pushes through Cloud Monitoring exporters.
- **API/Console:**
  ```bash
  curl -sS -H "X-API-KEY: dev-analyst-token" \
    "https://fastapi-gateway-y5jge5w2cq-uc.a.run.app/reports/dossiers?status=completed&limit=5" | \
    jq '{count, plans: [.items[].plan_id]}'
  ```
  Confirm the processed plan IDs appear in the console and that inline signature verification still passes.
- **Drive uploads:** Spot-check the Shared Drive parent (or staging bucket) to make sure new dossiers carry the current
  timestamp and hash manifest path.

## 5. Rollback plan
- Re-run `gcloud run jobs update dossier-queue --image <previous_tag>` to pull the last known-good digest.
- Set `I4G_DOSSIER__DRY_RUN=true` and execute the job once to ensure no partial artifacts exist.
- If templates or manifests regressed, revert the relevant commit and re-run the local + Cloud Run smoke steps before
  re-enabling production batches.
- Capture the rollback details (execution IDs, reason, operator) in `planning/change_log.md` and notify stakeholders via
  the normal incident channel.

## 6. References
- Smoke coverage (local + dev): [`docs/smoke_test.md`](../smoke_test.md#5-dossier-queue-job-local)
- Console workflow and verification: [`docs/runbooks/console/reports.md`](./console/reports.md)
- Subpoena handoff playbook: [`docs/runbooks/dossiers_subpoena_handoff.md`](./dossiers_subpoena_handoff.md)
- Terraform settings examples: `infra/environments/dev/terraform.tfvars`
