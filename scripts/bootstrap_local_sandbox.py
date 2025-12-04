#!/usr/bin/env python
"""Bootstrap helper for the local i4g sandbox environment.

This script orchestrates the existing developer utilities found in docs/dev_guide.md to
regenerate sample data (bundles, screenshots, indexes, review cases) so the local profile
(`I4G_ENV=local`) has realistic fixtures without manual command juggling.

Usage:
    python scripts/bootstrap_local_sandbox.py [--skip-ocr] [--skip-vector] [--reset]

Options:
    --skip-ocr:     Do not regenerate synthetic chat screenshots or run OCR.
    --skip-vector:  Skip rebuilding vector/structured demo stores; useful when only review
                    cases need refreshing.
    --reset:        Delete derived artifacts (chat_screens, OCR output, SQLite DB, vector store)
                    before regenerating.

Prerequisites:
    - Conda/venv with project dependencies installed.
    - Ollama running locally if summarization stages are executed later.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
DATA_DIR = ROOT / "data"
BUNDLES_DIR = DATA_DIR / "bundles"
CHAT_SCREENS_DIR = DATA_DIR / "chat_screens"
OCR_OUTPUT = DATA_DIR / "ocr_output.json"
SEMANTIC_OUTPUT = DATA_DIR / "entities_semantic.json"
MANUAL_DEMO_DIR = DATA_DIR / "manual_demo"
CHROMA_DIR = DATA_DIR / "chroma_store"
SQLITE_DB = DATA_DIR / "i4g_store.db"
REPORTS_DIR = DATA_DIR / "reports"
PILOT_CASES_PATH = MANUAL_DEMO_DIR / "dossier_pilot_cases.json"

DEFAULT_PILOT_CASES = [
    {
        "case_id": "dossier-pilot-001",
        "text": (
            'Victim met "Alex" on social media, was convinced to move retirement savings into a sham'
            " staking pool and wired funds to an exchange wallet controlled by the actor."
        ),
        "classification": "romance_investment",
        "confidence": 0.93,
        "loss_amount_usd": 185000,
        "jurisdiction": "US-CA",
        "victim_country": "US",
        "offender_country": "NG",
        "accepted_at": "2025-11-28T18:45:00Z",
        "entities": {
            "emails": ["finance@stellar-bonds.co"],
            "crypto_wallets": ["bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"],
            "phone_numbers": ["+14155550127"],
        },
        "metadata": {
            "loss_currency": "USD",
            "intake_source": "northern-ca-fiu-2024Q4",
            "summary": "High-dollar romance funnel leveraging fake staking dashboards.",
        },
        "dataset": "dossier-pilot",
        "notes": "High priority given LEA interest in the rom-invest pattern.",
    },
    {
        "case_id": "dossier-pilot-002",
        "text": (
            "Shell merchant SwiftCart Pay intercepted ACH settlements and rerouted them through a"
            " US correspondent account before laundering via MX fintech partners."
        ),
        "classification": "merchant_fraud",
        "confidence": 0.9,
        "loss_amount_usd": 76000,
        "jurisdiction": "US-TX",
        "victim_country": "US",
        "offender_country": "MX",
        "accepted_at": "2025-11-30T14:20:00Z",
        "entities": {
            "bank_accounts": ["021000021-99118822"],
            "emails": ["support@swiftcart-pay.com"],
            "urls": ["https://swiftcart-pay.com/settlements"],
        },
        "metadata": {
            "loss_currency": "USD",
            "sector": "ecommerce",
            "summary": "Merchant impersonation with mule-controlled correspondent account.",
        },
        "dataset": "dossier-pilot",
        "notes": "Useful for showcasing ACH + mule indicators in a single dossier.",
    },
    {
        "case_id": "dossier-pilot-003",
        "text": (
            'Telegram pump group "Phoenix Quant" raised BTC from UK victims and disappeared after'
            " promising weekly 20% returns; funds moved through UAE OTC brokers."
        ),
        "classification": "crypto_rugpull",
        "confidence": 0.92,
        "loss_amount_usd": 310000,
        "jurisdiction": "GB-LND",
        "victim_country": "GB",
        "offender_country": "AE",
        "accepted_at": "2025-12-01T09:05:00Z",
        "entities": {
            "telegram_handles": ["@phoenix_quant"],
            "emails": ["ops@phoenixquant.ai"],
            "crypto_wallets": ["3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"],
        },
        "metadata": {
            "loss_currency": "GBP",
            "summary": "Cross-border rug pull routed through UAE OTC brokers.",
            "intake_source": "uk-ncaa-2025W03",
        },
        "dataset": "dossier-pilot",
        "notes": "Demonstrates cross-border requirement hitting Europe to Gulf corridor.",
    },
]


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    """Execute a command, streaming stdout/stderr."""

    print("→", " ".join(cmd))
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    pythonpath_parts = [str(SRC_DIR)]
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    subprocess.run(cmd, cwd=cwd or ROOT, check=True, env=env)


def reset_artifacts(skip_ocr: bool, skip_vector: bool) -> None:
    """Remove generated artifacts so the sandbox refreshes cleanly."""

    if not skip_ocr:
        shutil.rmtree(CHAT_SCREENS_DIR, ignore_errors=True)
        if OCR_OUTPUT.exists():
            OCR_OUTPUT.unlink()
    if SEMANTIC_OUTPUT.exists():
        SEMANTIC_OUTPUT.unlink()
    if not skip_vector:
        shutil.rmtree(MANUAL_DEMO_DIR, ignore_errors=True)
        shutil.rmtree(CHROMA_DIR, ignore_errors=True)
        if SQLITE_DB.exists():
            SQLITE_DB.unlink()
    shutil.rmtree(REPORTS_DIR, ignore_errors=True)


def ensure_dirs() -> None:
    """Create data directories expected by downstream scripts."""

    for path in (BUNDLES_DIR, CHAT_SCREENS_DIR, REPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def build_bundles() -> None:
    run(
        [
            sys.executable,
            "tests/adhoc/build_scam_bundle.py",
            "--outdir",
            str(BUNDLES_DIR),
            "--chunk_chars",
            "800",
        ]
    )


def synthesize_screens() -> Path:
    bundles = sorted(BUNDLES_DIR.glob("*.jsonl"))
    if not bundles:
        raise RuntimeError("No bundle JSONL files found in data/bundles; rerun build step.")
    bundle = bundles[0]
    run(
        [
            sys.executable,
            "tests/adhoc/synthesize_chat_screenshots.py",
            "--input",
            str(bundle),
            "--limit",
            "20",
        ]
    )
    return bundle


def run_ocr() -> None:
    run(
        [
            sys.executable,
            "scripts/run_ocr.py",
            "--input",
            str(CHAT_SCREENS_DIR),
        ]
    )


def run_semantic_extraction() -> None:
    run(
        [
            sys.executable,
            "scripts/run_semantic_extraction.py",
        ]
    )


def rebuild_manual_demo() -> None:
    run(
        [
            sys.executable,
            "tests/adhoc/manual_ingest_demo.py",
            "--structured-db",
            str(SQLITE_DB),
            "--vector-dir",
            str(CHROMA_DIR),
        ]
    )


def ensure_pilot_cases_file() -> None:
    if PILOT_CASES_PATH.exists():
        return
    PILOT_CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PILOT_CASES_PATH.write_text(json.dumps(DEFAULT_PILOT_CASES, indent=2))
    print(f"🗂️  Seeded pilot cases config at {PILOT_CASES_PATH}")


def seed_review_cases() -> None:
    run(
        [
            sys.executable,
            "tests/adhoc/synthesize_review_cases.py",
            "--reset",
            "--queued",
            "5",
            "--in-review",
            "2",
            "--accepted",
            "1",
            "--rejected",
            "1",
        ]
    )


def apply_migrations() -> None:
    """Apply Alembic migrations before seeding structured data."""

    run(
        [
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
        ]
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap local sandbox data")
    parser.add_argument("--skip-ocr", action="store_true", help="Skip generating chat screenshots and OCR")
    parser.add_argument("--skip-vector", action="store_true", help="Skip rebuilding vector/structured demo stores")
    parser.add_argument("--reset", action="store_true", help="Remove derived artifacts before regenerating")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    ensure_dirs()

    if args.reset:
        reset_artifacts(skip_ocr=args.skip_ocr, skip_vector=args.skip_vector)

    apply_migrations()

    if not BUNDLES_DIR.exists() or not any(BUNDLES_DIR.glob("*.jsonl")):
        build_bundles()

    tesseract_available = shutil.which("tesseract") is not None
    if not args.skip_ocr:
        if not tesseract_available:
            print(
                "⚠️  Tesseract not found on PATH; skipping OCR and semantic extraction. Install it or rerun with --skip-ocr."
            )
        else:
            synthesize_screens()
            run_ocr()
            run_semantic_extraction()
    else:
        print("⚠️  Skipping OCR pipeline; existing artifacts will be reused if present.")

    if not args.skip_vector:
        rebuild_manual_demo()
    else:
        print("⚠️  Skipping vector/structured demo rebuild; existing stores assumed valid.")

    ensure_pilot_cases_file()

    seed_review_cases()

    print("✅ Local sandbox refreshed. Data directory:", DATA_DIR)


if __name__ == "__main__":
    main()
