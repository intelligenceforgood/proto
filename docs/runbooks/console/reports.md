# Console Runbook — Reports Tab (Evidence Dossiers)

Use the Reports → **Evidence Dossiers** view to inspect generated bundles, review manifest warnings, and verify the
signature manifest before handing dossiers to partners. The Streamlit panel remains available for backup, but the
Next.js console is the primary workflow going forward.

![Reports tab list view](../../assets/console/dossiers-list.png)

## Prerequisites
- Sign into the console at `https://i4g-console-y5jge5w2cq-uc.a.run.app/reports/dossiers` (IAP-protected).
- Ensure the dossier queue contains at least one `completed` plan. Run `i4g-admin process-dossiers --batch-size 3` or
  kick the Cloud Run job if the list is empty.
- Leave **Include manifest JSON** disabled unless you actively need to download the payload; streaming the JSON slows
  pagination on poor connections.

## 1. Filter and review the queue
1. Use the filter card to select a **Status** (Completed, Pending, Failed, All) and adjust the **Rows to load** limit.
   Submitting the form refreshes the list and updates the badge that reports how many plans were returned.
2. Each dossier card highlights:
   - Jurisdiction focus, loss totals, warning counts, and case chips.
   - Manifest and signature-manifest file paths (click the chips to copy paths into notes or tickets).
   - Bundle rationale (single jurisdiction, cross-border, etc.) and the timestamp from `generated_at`.
3. Expand **Dossier plan payload** when you need the raw JSON for audit notes or to confirm bundle membership.
4. Watch the warning badge. A yellow badge means the generator emitted warnings (missing structured context, zero-loss
   cases, etc.) and you should review the queue entry before exporting artifacts.

## 2. Verify signatures inline

![Signature verification panel](../../assets/console/dossiers-verify.png)

1. Click **Verify signatures** on any card. The console calls `/reports/dossiers/{plan_id}/verify` and locks the panel
   while hashing the referenced artifacts.
2. Read the banner result:
   - **Verified** — every artifact listed in `{plan_id}.signatures.json` exists and matches the stored hash.
   - **Attention** — at least one artifact is missing or mismatched. Inspect the table for the failing row.
3. The verification drawer lists each artifact with:
   - Expected hash prefix.
   - Actual hash prefix (or `missing`).
   - Size in bytes.
   - Absolute or shared-drive-relative path.
4. Capture the verification timestamp plus the leading eight hash characters in your analyst notes. Auditors expect to
   see both the console output and the `.signatures.json` file when reviewing a handoff.

## 3. Share dossiers with partners
1. Download the manifest, markdown dossier, and signature-manifest from the chips at the top of the card or from the
   side panel that opens after you click a file path.
2. When sending artifacts to LEA contacts, provide:
   - Markdown (or rendered PDF) dossier.
   - `.signatures.json` file.
   - Verification summary (status + timestamp + hash fragments) so the recipient can trust the handoff.
3. If **Include manifest JSON** was disabled during review, toggle it on, refresh the card, and repeat the verification
   step before capturing screenshots for compliance packages. This ensures the manifest preview you show matches what
   investigators will fetch from storage.
4. When the request is tied to a subpoena or court order, follow the
   [`dossiers_subpoena_handoff`](../dossiers_subpoena_handoff.md) playbook to package artifacts and log the delivery.

## 4. Troubleshooting
- **Missing cards** — queue probably lacks completed plans. Run the CLI job or confirm Cloud Run completed the latest
  batch.
- **Repeated verification failures** — re-run `i4g-admin process-dossiers --plan-id ...` to regenerate the manifest. If
  hashes still mismatch, check `data/reports/dossiers/{plan_id}` for partial files.
- **Slow manifest streaming** — leave the toggle off unless reviewing JSON payloads. Use the warning chips and signature
  verification for most triage flows.
- **Need a high-level overview?** The legacy Streamlit checklist lives in `docs/dev_guide.md` under "Streamlit dossier
  viewer", but the console workflow documented here replaces it for day-to-day use.

## 5. Related resources
- Dossier architecture and queue settings: `docs/dev_guide.md#streamlit-dossier-viewer--regression-checklist`.
- Bundle criteria and milestone scope: `planning/milestone4_agentic_evidence_dossiers.md`.
- Golden regression harness that backs the console data: `tests/unit/reports/test_dossier_golden_regression.py`.
- Subpoena handoff workflow: [`docs/runbooks/dossiers_subpoena_handoff.md`](../dossiers_subpoena_handoff.md).
