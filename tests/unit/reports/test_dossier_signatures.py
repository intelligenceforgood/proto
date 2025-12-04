"""Tests for dossier artifact signature helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from i4g.reports.dossier_signatures import generate_signature_manifest, verify_manifest_payload


def test_generate_signature_manifest_hashes_files(tmp_path) -> None:
    artifact = tmp_path / "sample.txt"
    artifact.write_text("dossier artifact")

    manifest = generate_signature_manifest([("manifest", artifact)])

    assert manifest.algorithm == "sha256"
    assert len(manifest.artifacts) == 1
    entry = manifest.artifacts[0]
    assert entry.path == artifact
    assert entry.size_bytes == artifact.stat().st_size
    expected_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert entry.hash_value == expected_hash


def test_generate_signature_manifest_handles_missing_files(tmp_path) -> None:
    missing = Path(tmp_path / "missing.txt")

    manifest = generate_signature_manifest([("missing", missing)])

    assert manifest.artifacts == ()
    assert manifest.warnings == (f"Artifact missing missing on disk at {missing}",)


def test_verify_manifest_payload_matches_hash(tmp_path) -> None:
    artifact = tmp_path / "signed.json"
    artifact.write_text("payload")
    expected_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest_payload = {
        "algorithm": "sha256",
        "artifacts": [
            {
                "label": "manifest",
                "path": str(artifact),
                "hash": expected_hash,
            }
        ],
        "warnings": [],
    }

    report = verify_manifest_payload(manifest_payload)

    assert report.all_verified is True
    assert report.missing_count == 0
    assert report.mismatch_count == 0
    artifact_report = report.artifacts[0]
    assert artifact_report.exists is True
    assert artifact_report.matches is True
    assert artifact_report.actual_hash == expected_hash


def test_verify_manifest_payload_detects_missing_file(tmp_path) -> None:
    manifest_payload = {
        "algorithm": "sha256",
        "artifacts": [
            {
                "label": "missing",
                "path": str(tmp_path / "missing.json"),
                "hash": "abc",
            }
        ],
    }

    report = verify_manifest_payload(manifest_payload)

    assert report.missing_count == 1
    assert report.artifacts[0].exists is False
    assert report.all_verified is False


def test_verify_manifest_payload_detects_mismatch(tmp_path) -> None:
    artifact = tmp_path / "signed.json"
    artifact.write_text("payload")
    manifest_payload = {
        "algorithm": "sha256",
        "artifacts": [
            {
                "label": "manifest",
                "path": str(artifact),
                "hash": "deadbeef",
            }
        ],
    }

    report = verify_manifest_payload(manifest_payload)

    assert report.mismatch_count == 1
    assert report.artifacts[0].matches is False
    assert report.all_verified is False
