# Analyst Runbook Index

This index replaces the monolithic hybrid-search guide. Each console tab now owns a dedicated runbook under
`docs/runbooks/console/` so updates stay scoped to the workflow you are touching. Read the sections that match the UI tab
or API endpoint you plan to operate.

## Console tabs
- **Search tab:** [`docs/runbooks/console/search.md`](./runbooks/console/search.md)
  - Refresh `/reviews/search/schema`, compose hybrid payloads, run smoke tests, and migrate saved searches.
- **Reports tab (Evidence Dossiers):** [`docs/runbooks/console/reports.md`](./runbooks/console/reports.md)
  - Inspect dossier cards, review manifest warnings, run in-console signature verification, and capture screenshots for
    compliance packages.
- **Upcoming tabs:** Additional guides for History and Tasks will land once those routes move out of beta. Track the
  work in `planning/milestone4_agentic_evidence_dossiers.md`.

## Supporting references
- Access control and API prerequisites: `docs/iam.md`.
- Developer deep dive (queue processor, Streamlit fallback, env vars): `docs/dev_guide.md`.
- Milestone context and regression checklist: `planning/milestone4_agentic_evidence_dossiers.md` and
  `planning/change_log.md`.
- Subpoena handoffs and evidence packaging: [`docs/runbooks/dossiers_subpoena_handoff.md`](./runbooks/dossiers_subpoena_handoff.md).

Keep this index updated whenever you add a new console workflow so analysts can jump directly to the relevant playbook.

