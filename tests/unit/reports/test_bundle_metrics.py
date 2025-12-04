"""Tests for shared dossier bundle metrics helpers."""

from __future__ import annotations

from decimal import Decimal

from i4g.reports.bundle_metrics import compute_bundle_metrics


def test_compute_bundle_metrics_derives_expected_fields() -> None:
    metadata = {
        "loss_amount_usd": 75000,
        "jurisdiction": "US-NY",
        "victim_country": "US",
        "scammer_country": "RU",
    }

    metrics = compute_bundle_metrics(metadata)

    assert metrics.loss_amount_usd == Decimal("75000")
    assert metrics.loss_band == "50k-100k"
    assert metrics.cross_border is True
    assert metrics.geo_bucket == "US"
    payload = metrics.to_dict()
    assert payload["loss_amount_usd"] == float(Decimal("75000"))


def test_compute_bundle_metrics_handles_missing_metadata() -> None:
    metrics = compute_bundle_metrics(None)

    assert metrics.loss_amount_usd == Decimal("0")
    assert metrics.loss_band == "unknown"
    assert metrics.cross_border is False
    assert metrics.geo_bucket == "UNKNOWN"
