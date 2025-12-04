"""Reusable helpers that derive dossier bundling metrics from metadata."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

_LOSS_BAND_EMPTY = "unknown"


@dataclass(frozen=True)
class BundleMetrics:
    """Derived metadata that powers dossier bundling decisions."""

    loss_amount_usd: Decimal
    loss_band: str
    jurisdiction: str
    geo_bucket: str
    victim_country: Optional[str]
    offender_country: Optional[str]
    cross_border: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable payload for storage layers."""

        return {
            "loss_amount_usd": float(self.loss_amount_usd),
            "loss_band": self.loss_band,
            "jurisdiction": self.jurisdiction,
            "geo_bucket": self.geo_bucket,
            "victim_country": self.victim_country,
            "offender_country": self.offender_country,
            "cross_border": self.cross_border,
        }


def compute_bundle_metrics(metadata: Mapping[str, Any] | None) -> BundleMetrics:
    """Derive bundle metrics from structured metadata."""

    payload = metadata or {}
    loss_amount = _parse_loss(payload)
    jurisdiction = _first_value(payload, ("jurisdiction", "victim_jurisdiction", "victim_state", "victim_country"))
    jurisdiction = (jurisdiction or "unknown").strip()
    victim_country = _normalize_country(
        _first_value(payload, ("victim_country", "victim_state", "jurisdiction_country"))
    )
    offender_country = _normalize_country(
        _first_value(payload, ("offender_country", "scammer_country", "jurisdiction_country"))
    )
    cross_border = bool(victim_country and offender_country and victim_country != offender_country)
    geo_bucket = _derive_geo_bucket(jurisdiction, victim_country)
    loss_band = _loss_band(loss_amount)
    return BundleMetrics(
        loss_amount_usd=loss_amount,
        loss_band=loss_band,
        jurisdiction=jurisdiction or "unknown",
        geo_bucket=geo_bucket,
        victim_country=victim_country,
        offender_country=offender_country,
        cross_border=cross_border,
    )


def _parse_loss(metadata: Mapping[str, Any]) -> Decimal:
    for key in ("loss_amount_usd", "loss_usd", "loss_amount", "loss"):
        value = metadata.get(key)
        if value is None:
            continue
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            continue
    return Decimal("0")


def _first_value(metadata: Mapping[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_country(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = value.strip().upper()
    return normalized or None


def _derive_geo_bucket(jurisdiction: str, victim_country: Optional[str]) -> str:
    token = jurisdiction.strip()
    if token:
        if "-" in token:
            prefix = token.split("-", 1)[0]
            if prefix:
                return prefix.upper()
        return token.upper()
    if victim_country:
        return victim_country
    return "UNKNOWN"


def _loss_band(loss_amount: Decimal) -> str:
    if loss_amount is None:
        return _LOSS_BAND_EMPTY
    if loss_amount >= Decimal("250000"):
        return "250k-plus"
    if loss_amount >= Decimal("100000"):
        return "100k-250k"
    if loss_amount >= Decimal("50000"):
        return "50k-100k"
    if loss_amount == Decimal("0"):
        return _LOSS_BAND_EMPTY
    return "below-50k"


__all__ = ["BundleMetrics", "compute_bundle_metrics"]
