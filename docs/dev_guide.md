# Developer Guide — Intelligence for Good (i4g)

This Developer Guide explains how to set up, run, and extend the i4g platform — an experimental system for detecting, analyzing, and reporting online scams (especially crypto and romance scams targeting seniors).

---

## System Overview

i4g combines OCR, language models, semantic entity extraction, classification, and human-in-the-loop review to support law enforcement and user assistance workflows.

### Core Data Flow

```mermaid
graph TD
    A["Chat Screenshots"] --> B["OCR (Tesseract)"]
    B --> C["Semantic Extraction<br/>(LangChain + Ollama)"]
    C --> D["Classification<br/>(Rule + LLM)"]
    D --> E["Ingestion to Knowledge Base"]
    E --> F["Analyst Review API / Dashboard"]
    F --> G["Law Enforcement Report Generator"]
```

---

## Project Layout

```
i4g/
├── src/i4g/
│   ├── ocr/                # OCR via Tesseract
│   ├── extraction/         # NER + Semantic Extraction
│   ├── classification/     # Fraud classifiers
│   ├── embedding/          # Vector embeddings
│   ├── store/              # Vector + Structured + Review DB
│   ├── rag/                # RAG pipeline & retrieval
│   ├── reports/            # Report generation & GDoc export
│   ├── review/             # FastAPI review service
│   ├── worker/             # Background tasks (report gen)
│   └── ...
├── data/                   # Runtime artifacts (bundles, chat_screens, reports, SQLite)
├── tests/
│   ├── unit/
│   └── adhoc/
├── templates/              # Jinja2 report templates
└── scripts/                # Production/admin CLI utilities
```

**Operational vs. Developer Utilities**

- `scripts/`: automation you can run in production or staging (OCR pipeline, semantic extraction, index rebuilds).
- `tests/adhoc/`: developer-only demos, diagnostics, data synthesizers, and lightweight utilities (including context snapshots).

---

## Local Datastore

The `data/i4g_store.db` file is a local SQLite database used for storing application data, including:

-   **Structured Data:** Extracted entities, classification results, and other structured information.
-   **Vector Embeddings:** Embeddings of text for semantic search.
-   **Review Data:** Data for the analyst review system.

This file is created automatically when you run the application and is not meant to be shared or committed to version control. If you want to start with a fresh database, you can delete this file.

---

## Setup Instructions

### Prerequisites

- macOS or Linux (Apple Silicon M3 tested)
- Python ≥ 3.11
- Tesseract OCR installed (`brew install tesseract`)
- Ollama running locally (`ollama serve`)
- FAISS (for vector store)
- Optional: Google Cloud SDK (for GDoc export)

### Environment Setup

```bash
git clone https://github.com/jsoung/i4g.git
cd i4g
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
pip install pre-commit

# Optional: install the shared pre-commit hook for formatting + unit tests
ln -sf ../../scripts/git-hooks/pre-commit .git/hooks/pre-commit
pre-commit run --all-files  # prime caches on first install
```

To test your setup:

```bash
pytest -q
```

## Configuration

i4g services load configuration through `i4g.settings`, which is powered by
[Pydantic settings](https://docs.pydantic.dev/latest/usage/pydantic_settings/).
Values are resolved in the following order:

1. Defaults declared in the `Settings` model.
2. Direct environment variables (`I4G_*`).
3. Project `.env` files (`.env`, `.env.<env>`, `.env.local`).
4. TOML config files (`I4G_SETTINGS_FILE`, `config/settings.local.toml`, `config/settings.default.toml`).
5. Secret files (when `SECRETS_*` points to a directory of files).

The active environment is selected via `I4G_ENV` (defaults to `local`). All
services—including Streamlit, FastAPI, and CLI tools—should read configuration
via `get_settings()` instead of accessing environment variables directly.

### Sample local override

```
# .env.local
I4G_ENV=local
I4G_API_URL=http://127.0.0.1:8000
I4G_API_KEY=dev-analyst-token
I4G_VECTOR_BACKEND=chroma
```

### Local sandbox env vars (FastAPI, Streamlit, Next.js)

When the sandbox services cannot find the expected API base/key, they usually crash with cryptic errors (Streamlit shows
`ConnectionError: Failed to reach API`, the Next.js console surfaces `fetch failed`, and FastAPI background workers log
`NoneType` attribute errors). Avoid that loop by defining the following variables before starting any of the local apps.
Use a `.env.local` file or export them in the same shell session:

| Service | Launch command | Required env vars |
| --- | --- | --- |
| **FastAPI** | `conda run -n i4g uvicorn i4g.api.app:app --reload` | `I4G_ENV=local`, `I4G_API_URL=http://127.0.0.1:8000`, `I4G_API_KEY=dev-analyst-token` |
| **Streamlit dashboard** | `conda run -n i4g streamlit run src/i4g/ui/analyst_dashboard.py` | All FastAPI vars **plus** `I4G_API_KIND=proto` so the dashboard uses the proto search contract |
| **Next.js analyst console** | `pnpm --filter web dev` | Same API vars as above, `I4G_API_KIND=proto`, and set `NEXT_PUBLIC_USE_MOCK_DATA=false` so the console skips the mock client |

Additional tips:

- Keep `I4G_ENV=local` for all sandbox runs; it forces SQLite/Chroma backends and mock identity.
- If you need to point the console or Streamlit at `i4g-dev`, override `I4G_ENV=dev` **and** supply the matching
    `I4G_API_KEY` (for example from Secret Manager). Missing keys produce `401 Unauthorized` loops in both UIs.
- Add these exports to `.env.local` (proto) and `.env.local` inside `ui/apps/web/` if you frequently switch between
    services; both repos load the files automatically via `python-dotenv` / Next.js env loaders.
- Whenever you see Playwright/Vitest failures about `fetch` or Streamlit reports "API unavailable", re-check that
    `I4G_API_URL` and `I4G_API_KEY` are present in the environment running that command (VS Code tasks do not inherit
    shell profiles by default).

### TOML configuration files

When you find yourself exporting a dozen env vars before every command, move
those values into TOML config files. The repo ships with
`config/settings.default.toml` for shared defaults and git-ignores
`config/settings.local.toml` so laptops can define their own overrides. Both
files use the same nesting as `Settings`, so the snippet below captures the
common dev ingestion profile:

```toml
# config/settings.local.toml
env = "dev"

[storage]
firestore_project = "i4g-dev"

[vector]
vertex_ai_project = "i4g-dev"
vertex_ai_data_store = "retrieval-poc"

[ingestion]
default_dataset = "retrieval_poc_dev"
enable_firestore = true
enable_vertex = true
```

Values from `settings.local.toml` override the tracked defaults but remain
below explicit env vars, so ad-hoc overrides still work. To test a one-off file
without renaming it, point `I4G_SETTINGS_FILE` at the desired TOML path before
running a command.

To inspect the effective configuration:

```bash
python -c "from i4g.settings import get_settings; print(get_settings())"
```

### Nested settings reference

`i4g.settings` now groups related configuration into typed sections so we can swap individual services (local versus GCP) without flipping the entire environment. The most frequently used sections are:

| Section | Purpose | Common overrides |
| --- | --- | --- |
| `settings.api` | FastAPI/CLI endpoints and shared API key | `I4G_API__BASE_URL`, `I4G_API__KEY` |
| `settings.storage` | Structured store backend and Cloud Storage buckets | `I4G_STORAGE__STRUCTURED_BACKEND`, `I4G_STORAGE__SQLITE_PATH`, `I4G_STORAGE__EVIDENCE_BUCKET` |
| `settings.vector` | Vector index backend and embedding model | `I4G_VECTOR__BACKEND`, `I4G_VECTOR__CHROMA_DIR`, `I4G_VECTOR__PGVECTOR__DSN` |
| `settings.llm` | Chat/RAG model provider selection | `I4G_LLM__PROVIDER`, `I4G_LLM__OLLAMA_BASE_URL`, `I4G_LLM__VERTEX_AI__MODEL` |
| `settings.identity` | Auth provider wiring for Streamlit + APIs | `I4G_IDENTITY__PROVIDER`, `I4G_IDENTITY__AUDIENCE` |
| `settings.ingestion` | Scheduler + Cloud Run job defaults | `I4G_INGESTION__ENABLE_SCHEDULED_JOBS`, `I4G_INGESTION__SERVICE_ACCOUNT` |

Use double underscores to drill into nested fields (for example `I4G_STORAGE__EVIDENCE_BUCKET=i4g-evidence-staging`). Legacy aliases like `I4G_API_URL` and `I4G_VECTOR_BACKEND` still work, so existing `.env` files do not need to change immediately.


## Running the Core Pipelines

There are also targeted demos under `tests/adhoc/` if you want to exercise a single feature (OCR, extraction, reporting, etc.); check the README in that folder for usage details.

## Database Migrations

Dual-extraction work now ships with Alembic migrations. Apply the latest schema before running
local demos or CLI jobs:

```bash
python -m alembic upgrade head
```

The command automatically targets the SQLite path from `settings.storage.sqlite_path`. To apply
migrations against Cloud SQL/AlloyDB, export `I4G_DATABASE_URL` (or `ALEMBIC_DATABASE_URL`) with a
Postgres connection string and rerun the upgrade command.

`scripts/bootstrap_local_sandbox.py` runs migrations for you, but keep the command above handy for
CI/CD and ops workflows.

## Restoring Sample Data & Artifacts

If you cleaned out the `data/` directory (or are onboarding to a fresh clone), start with the consolidated helper below to rebuild everything in one step:

```bash
python scripts/bootstrap_local_sandbox.py --reset
```

This command orchestrates the bundle builder, screenshot synthesis, OCR, semantic extraction, manual ingest demo, and review-case seeding. It automatically adds the project `src/` folder to `PYTHONPATH`, so running from an editable install (`pip install -e .`) is optional. If Tesseract is missing it falls back to the downstream steps and prints a reminder—you can rerun with `--skip-ocr` if you plan to install OCR tooling later.

Key switches:
- `--skip-ocr`: bypass screenshot synthesis, OCR, and semantic extraction (useful when you only need fresh vector/structured stores or do not have Tesseract installed yet).
- `--skip-vector`: skip the manual ingest demo; existing Chroma/SQLite artifacts will remain untouched.
- `--reset`: remove generated artifacts before rebuilding; omit for an incremental refresh.

The first run primes embeddings and Chroma metadata, so it can take a couple of minutes. Subsequent runs reuse cached models and complete faster.

Prefer a manual walkthrough or need to re-run a single stage? Use the individual helpers below.

1. **Download + Normalize Scam Text Bundles**

    ```bash
    python tests/adhoc/build_scam_bundle.py --outdir data/bundles --chunk_chars 800
    ```

    This pulls public-domain scam/phishing corpora via Hugging Face, masks obvious PII, and writes JSONL bundles such as `data/bundles/ucirvine_sms.jsonl` and `data/bundles/bundle_all.jsonl`.

2. **Synthesize Chat Screenshots for OCR Demos**

    ```bash
    python tests/adhoc/synthesize_chat_screenshots.py --input data/bundles/ucirvine_sms.jsonl --limit 20
    ```

    Generated PNGs land in `data/chat_screens/`. That folder is the expected input directory for the OCR script, so you can immediately run:

    ```bash
    python scripts/run_ocr.py --input data/chat_screens
    ```

3. **Reprime SQLite / Vector Stores (optional)**

    ```bash
    python tests/adhoc/manual_ingest_demo.py
    ```

    This seeds `data/manual_demo/` with a structured SQLite DB and a Chroma vector index using two representative scam cases. Rerun as needed whenever you want a clean slate for ingestion demos.

4. **Seed Analyst Review Queue**

    ```bash
    python tests/adhoc/synthesize_review_cases.py --reset --queued 5 --in-review 2 --accepted 1 --rejected 1
    ```

    Populates `data/i4g_store.db` with synthetic review items so the Streamlit dashboard has cases ready to demonstrate claim/accept/reject flows.

Once these assets exist, the downstream scripts referenced below will find usable inputs without manual data hunting.

## Vertex AI Search Retrieval PoC

For the hosted Discovery workflows (Terraform setup, ingestion script, CLI query helpers, filters, and boosting examples) see `docs/retrieval_gcp_guide.md`. That guide tracks the GCP environment separately from the local sandbox to keep each workflow focused.

### Hugging Face API Tokens

Some ad-hoc scripts (for example `tests/adhoc/hf_embedding.py`) call the Hugging Face Inference API. Create a personal access token at <https://huggingface.co/settings/tokens> and expose it before running those scripts:

```bash
export HF_API_TOKEN=hf_xxx    # put this in your shell profile if you use it often
python tests/adhoc/hf_embedding.py "Sample text to embed"
```

Without the token, the API returns `401 Invalid credentials`.

### OCR + Extraction

```bash
python scripts/run_ocr.py --input data/chat_screens
```

That path is where the synthetic screenshots land; swap in another directory if you are running OCR on real evidence.

### Semantic NER + Classification

```bash
python scripts/run_semantic_extraction.py
python tests/adhoc/classify_text.py "This looks like a scam."
```

`run_semantic_extraction.py` reads the OCR output saved to `data/ocr_output.json` by the previous step and writes enriched entities to `data/entities_semantic.json`.

### Scam Detection RAG Query

```bash
i4g-admin query --question "Is this a crypto scam?"
```

---

## Analyst Review System

### Backend API

Run the review backend:

```bash
uvicorn i4g.api.app:app --reload
```

Check endpoint:
```
http://localhost:8000/docs
```

### Streamlit Analyst Dashboard

```bash
streamlit run tests/adhoc/analyst_dashboard_demo.py
```

Note: If Streamlit fails to import protobuf with an error like "Descriptors cannot be created directly", pin protobuf to a 4.x release before launching Streamlit. For example:

```bash
pip install 'protobuf>=4.21.0,<5.0'
```

For full dashboard functionality (API-backed), you can run the main UI:

```bash
streamlit run src/i4g/ui/analyst_dashboard.py

Developer note: dependency resolution

`requirements.txt` is generated with `pip-compile --extra=test pyproject.toml`. Thanks to the `protobuf>=5,<6` pin, the resolver should converge without manual edits. Run the command inside the `i4g` conda environment so the right interpreter is on PATH, and commit both `pyproject.toml` and `requirements.txt` when you intentionally update dependencies.
```

**Streamlit Analyst Dashboard**

- **Seed data first:** `python tests/adhoc/synthesize_review_cases.py --reset --queued 5` so the queue has items to triage.
- **Launch UI:** `streamlit run tests/adhoc/analyst_dashboard_demo.py` (full workflow for claiming, accepting, and rejecting cases).
- **Search faster:** blend vector + structured lookups, adjust result counts, and paginate without re-running queries; hits tally shows total coverage.
- **Saved search controls:** tag-based grouping, quick presets, CSV export, rename/share/delete, and bulk tag edits directly in the sidebar.
- **Audit trail:** every run lands in `/reviews/search/history`; preview previous runs or replay them from the recent history panel.

---

## Report Generation

### Manual Report Preview

```bash
python tests/adhoc/manual_report_demo.py
```

### GDoc Export (if Google API configured)

```bash
python tests/adhoc/manual_report_export_demo.py
```

Generated `.docx` files are written to `data/reports/` whether you trigger them from the demos, the Streamlit dashboard, or the API.

---

## Developer Utilities

**Core Scripts**

- **Build FAISS index:** `python scripts/build_index.py --backend faiss --reset`
- **Run unit tests:** `pytest -v`
- **Format source:** `black src tests`
- **Snapshot context:** `bash tests/adhoc/generate_context_snapshot.sh --help`
- **Install git hook:** `ln -sf ../../scripts/git-hooks/pre-commit .git/hooks/pre-commit`
- **First hook run:** `pre-commit run --all-files`

For a consolidated checklist of ingestion and intake smoke tests, see [`docs/smoke_test.md`](./smoke_test.md).

## Analyst Console (Next.js UI)

The analyst console lives in the sibling `ui/` workspace (PNPM + Turborepo). Install dependencies at the repo root with `pnpm install`, then run `pnpm --filter web dev` to launch the Next.js app. The console talks to the proto API directly; set `I4G_API_URL`/`I4G_API_KEY` (or rely on the mock client) before starting the server.

### UI test strategy

1. **Unit tests (Vitest + Testing Library).** Run `pnpm --filter web test` to exercise hooks, utilities, and React components under jsdom. Place new specs alongside the module under `apps/web/src/` or in `apps/web/tests/unit/`. Keep coverage for entity-filter builders, API payload helpers, and any non-trivial state transitions. The UI git hook enforces this by default; export `I4G_UI_PRECOMMIT_QUICK=1` only when you need a temporary lint-only pass, and make sure to rerun the full suite before pushing or opening a PR.
2. **Smoke tests (Playwright).** Run `pnpm --filter web test:smoke` for a lightweight end-to-end check that boots `next dev` and opens `/search`. Extend `apps/web/tests/smoke/` whenever you add a new route or major workflow so deploys retain at least one canary scenario. Install browsers once per machine with `pnpm --filter web exec playwright install --with-deps`. Treat Playwright as mandatory before releases, after changing routing/server-actions, or when saved-search/history/filters touch backend contracts.

Both commands respect the same env vars as Next.js, so you can point the suite at `i4g-dev` by exporting `I4G_API_URL`/`I4G_API_KEY` beforehand. Prefer running the unit suite locally before every commit and the smoke tests before cutting a release or merging sizable UI work.

> ⚠️ Run commits from an activated `i4g` environment (or set `CONDA_PREFIX`/`VIRTUAL_ENV`) so the hook uses the correct Python when executing unit tests.
> Formatter line length is set to 120 characters to match team editor settings.

**Saved Search Admin (`i4g-admin`)**

- `i4g-admin export-saved-searches --owner alice --output alice.json`
- `i4g-admin import-saved-searches --shared --input team.json`
- `i4g-admin bulk-update-tags --owner alice --tags urgent wallet --remove legacy`
- `i4g-admin prune-saved-searches --owner alice --tags legacy`

Install locally with `pip install -e .` to expose `i4g-admin` everywhere; run `i4g-admin --help` to browse each subcommand.

---

## Notes for Cloud Deployment

- Production environment expected to run on Linux (GCP or AWS).
- Review API and worker tasks should run as services.
- GDoc exporter requires Google service account credentials.
- Use `docker/` folder for containerized deployment (optional).

### Terraforming GCP Resources

Infrastructure lives under the sibling `infra/` tree. For module layout, workflow steps, and Discovery prerequisites (including the quota-project command), follow the instructions in `infra/README.md`.

### Publishing the FastAPI Image to Artifact Registry

Manual workflow (run from the repo root unless noted):

1. Authenticate gcloud and Docker (once per machine/shell).

    gcloud config set project i4g-dev
    gcloud auth login
    gcloud auth configure-docker us-central1-docker.pkg.dev

2. Create the Artifact Registry repo if it does not already exist.

### Rapid Streamlit Redeploy (dev)

When you need to re-test Streamlit changes quickly, you can reuse the `streamlit:dev` tag standing in Terraform:

1. Rebuild and push the image:

        ```bash
        docker buildx build \
            --platform linux/amd64 \
            -f docker/streamlit.Dockerfile \
            -t us-central1-docker.pkg.dev/i4g-dev/applications/streamlit:dev \
            --push .
        ```

2. Force Cloud Run to pull the new digest, keeping existing env vars and supplying the Discovery defaults:

    ```bash
        gcloud run services update streamlit-analyst-ui \
            --project i4g-dev \
            --region us-central1 \
            --image us-central1-docker.pkg.dev/i4g-dev/applications/streamlit:dev \
            --update-env-vars I4G_VERTEX_SEARCH_PROJECT=i4g-dev,\
                I4G_VERTEX_SEARCH_LOCATION=global,\
                I4G_VERTEX_SEARCH_DATA_STORE=retrieval-poc,\
                I4G_VERTEX_SEARCH_SERVING_CONFIG=default_search
    ```

    `--update-env-vars` patches Cloud Run’s configuration without wiping values that Terraform applied earlier. Cloud Run reuses the same `:dev` tag but pulls the new digest.

Terraform keeps pointing at the same tag, so it will not trigger a deployment on its own. Once you finish iterating, bump the tag (and mirror the env vars) in `infra/environments/dev/terraform.tfvars` so infrastructure runs remain declarative.

    gcloud artifacts repositories create applications \
      --repository-format docker \
      --location us-central1

3. Build, tag, and push the service image. On Apple Silicon (M-series), target `linux/amd64` so Cloud Run can start the container.
```bash
docker buildx create --use --name i4g-builder # one-time builder
```
```bash
docker buildx inspect --bootstrap # optional: verify builder
```
```bash
docker buildx build \
--platform linux/amd64 \
-f docker/fastapi.Dockerfile \
-t us-central1-docker.pkg.dev/i4g-dev/applications/fastapi:dev \
--push \
.
```
For x86 development machines, plain docker build/tag/push still works:
```bash
docker build -f docker/fastapi.Dockerfile -t fastapi:dev .
docker tag fastapi:dev us-central1-docker.pkg.dev/i4g-dev/applications/fastapi:dev
docker push us-central1-docker.pkg.dev/i4g-dev/applications/fastapi:dev
```

Swap `docker/fastapi.Dockerfile` with `docker/streamlit.Dockerfile` and adjust the tag (e.g. `streamlit:dev`) to publish the Streamlit UI container using the same flow.

4. Reference the pushed URI in Terraform (for example `infra/environments/dev/terraform.tfvars`).

    project_id    = "i4g-dev"
    fastapi_image = "us-central1-docker.pkg.dev/i4g-dev/applications/fastapi:dev"

5. If you rebuild an image but keep the same tag (for example `:dev`), force Cloud Run to pull the new digest:

                gcloud run services update streamlit-analyst-ui \
                    --region=us-central1 \
                    --image=us-central1-docker.pkg.dev/i4g-dev/applications/streamlit:dev

                gcloud run services update fastapi-gateway \
                    --region=us-central1 \
                    --image=us-central1-docker.pkg.dev/i4g-dev/applications/fastapi:dev

        Running `terraform apply` afterwards keeps state in sync once the new revisions are live.

### Publishing the Cloud Run Job Images

Use the same `docker buildx` workflow to publish the ingestion, intake, and report job containers referenced by Terraform. Replace the Dockerfile path and tag as shown below, then update `infra/environments/*/terraform.tfvars` if you promote a new tag:

```bash
docker buildx build \
    --platform linux/amd64 \
        -f docker/ingest-job.Dockerfile \
        -t us-central1-docker.pkg.dev/i4g-dev/applications/ingest-job:dev \
    --push \
    .

docker buildx build \
    --platform linux/amd64 \
        -f docker/intake-job.Dockerfile \
        -t us-central1-docker.pkg.dev/i4g-dev/applications/intake-job:dev \
    --push \
    .

docker buildx build \
    --platform linux/amd64 \
        -f docker/report-job.Dockerfile \
        -t us-central1-docker.pkg.dev/i4g-dev/applications/report-job:dev \
    --push \
    .
```

By default the ingestion job runs against `/app/data/retrieval_poc/cases.jsonl` packaged in the container and skips embeddings unless `I4G_INGEST__ENABLE_VECTOR=true` is supplied, allowing the Cloud Run job to succeed without an external Ollama endpoint. Terraform injects `I4G_ENV` and the Dockerfile sets `I4G_RUNTIME__PROJECT_ROOT=/app`, so structured-store writes land in `/app/data` unless overridden.

> ⚠️  Reminder: `data/retrieval_poc` only seeds the prototype. Plan to remove the dataset from local workstations and job images once production ingestion sources are wired up.
