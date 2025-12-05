# Evidence Dossier Subpoena Handoff Playbook

Use this runbook whenever an agency delivers a subpoena or court order requesting dossier artifacts. The goal is to
validate the request, package verified evidence, and capture a complete audit trail without rerunning the agent unless
absolutely necessary.

## Prerequisites
- Confirm you can access the Next.js analyst console (`/reports/dossiers`) and the Shared Drive parent configured via
  `I4G_REPORT__DRIVE_PARENT_ID`.
- Keep the Streamlit dashboard available as a fallback, but prioritize the console workflow documented below.
- Ensure you have write access to the subpoena log (`docs/compliance.md` or the equivalent ticket system) so the handoff
  is traceable.

## 1. Validate the subpoena
1. Confirm the request arrived through an approved channel (agency portal, secure email, or registered mail). Reject
   informal requests immediately.
2. Verify sender identity via the contact roster. Call the agency desk line if signatures or letterhead look suspicious.
3. Record the request in the subpoena log with date, requesting agency, contact info, and the referenced case IDs or
   docket number.
4. Attach a scanned copy (PDF) of the subpoena to the log entry. Store the document in the same Shared Drive folder as
   the dossier artifacts.

## 2. Locate and verify the dossier
1. Open the console Reports → **Evidence Dossiers** view and filter by plan ID, jurisdiction, or status.
2. Expand the dossier card to confirm the cases in the bundle match the subpoena scope. Capture a screenshot showing the
   case chips and loss totals.
3. Click **Verify signatures**. Wait for the inline verification drawer to report **Verified**. Copy the timestamp and the
   first eight characters of each hash into your notes.
4. If verification fails:
   - Re-run the job locally: `conda run -n i4g i4g-dossier-job --plan-id <id>` (or via the admin helper) to regenerate the
     manifest.
   - Use the CLI helper to re-check the signature file:
     ```bash
     conda run -n i4g python - <<'PY'
     from pathlib import Path
     from i4g.reports.dossier_signatures import read_signature_manifest, verify_manifest_payload

     signature = read_signature_manifest(Path("data/reports/dossiers/<plan_id>.signatures.json"))
     report = verify_manifest_payload(signature, base_path=Path("data/reports/dossiers"))
     print(report)
     PY
     ```
   - Document the remediation in the subpoena log before continuing.

## 3. Assemble the evidence package
Prepare a folder named `subpoena-<agency>-<yyyymmdd>` under the Shared Drive parent. Include the following artifacts:

| Artifact | Source | Notes |
| --- | --- | --- |
| Markdown dossier (`<plan_id>.md`) or rendered PDF | `data/reports/dossiers/` (local) or console download chip | Convert to PDF when recipients cannot open Markdown. |
| JSON manifest (`<plan_id>.json`) | Console download chip / filesystem | Confirms plan metadata, context, tool outputs, and signature manifest location. |
| Signature manifest (`<plan_id>.signatures.json`) | Console download chip / filesystem | Required for downstream verification. |
| Verification summary (`verification-<plan_id>.txt`) | Manual note | Record timestamp, operator, tool used (console or CLI), and hash prefixes. |
| Subpoena scan (`subpoena-<reference>.pdf`) | Intake log | Tie the legal request to the delivered artifacts. |
| Access log excerpt (optional) | Task_STATUS UI or `scripts/verify_ingestion_run.py` output | Helps agencies confirm processing history when subpoena references a prior ticket. |

Include any supporting attachments referenced in the manifest (charts, GeoJSON, timeline PNGs). Use manifest-relative paths
so investigators can locate the files quickly.

## 4. Deliver and log
1. Upload the folder to the Shared Drive parent and restrict access to the requesting agency’s group. When possible,
   share a Drive link instead of emailing attachments.
2. Send the agency a short cover email referencing the subpoena number, dossier IDs, and Drive link. Include the
   verification summary in the message body.
3. Update the subpoena log entry with:
   - Drive link and permissions granted.
   - Verification timestamp and operator initials.
   - Any warnings noted in the manifest or verification output.
4. Notify compliance or legal contacts if the subpoena scope exceeds the requested dossiers or if the agency asks for raw
   evidence files outside the manifest.

## 5. Escalations and troubleshooting
- **Missing artifacts:** Regenerate the dossier (local or Cloud Run). If regeneration fails, attach the error logs to the
  subpoena log and notify engineering.
- **Signature mismatch after regeneration:** Re-run `verify_manifest_payload` directly against the regenerated files. If
  mismatches persist, mark the plan as `failed` in the console and escalate to engineering before sharing anything.
- **Agency requests new bundles:** Direct them to resubmit via the intake process. Only satisfy the subpoena scope in this
  runbook.

## References
- Console workflow: [`docs/runbooks/console/reports.md`](./console/reports.md)
- Manifest verification helper: `src/i4g/reports/dossier_signatures.py`
- Cloud Run smoke + regeneration steps: [`docs/smoke_test.md`](../smoke_test.md#8-dossier-queue-job-dev)
- Milestone tracker: [`planning/milestone4_agentic_evidence_dossiers.md`](../../planning/milestone4_agentic_evidence_dossiers.md)
