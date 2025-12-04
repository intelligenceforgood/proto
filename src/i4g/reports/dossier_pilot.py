"""Pilot dossier helpers for seeding sample cases and scheduling plans."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from i4g.reports.bundle_builder import BundleBuilder, BundleCriteria, DossierCandidate
from i4g.reports.bundle_candidates import BundleCandidateProvider
from i4g.services.factories import (
    build_bundle_builder,
    build_bundle_candidate_provider,
    build_review_store,
    build_structured_store,
)
from i4g.settings import get_settings
from i4g.store.review_store import ReviewStore
from i4g.store.schema import ScamRecord
from i4g.store.structured import StructuredStore

SETTINGS = get_settings()
DEFAULT_PILOT_CASES_PATH = SETTINGS.project_root / "data" / "manual_demo" / "dossier_pilot_cases.json"


@dataclass(frozen=True)
class PilotCaseSpec:
    """Serializable payload describing a pilot dossier case."""

    case_id: str
    text: str
    classification: str
    confidence: float
    loss_amount_usd: Decimal
    jurisdiction: str
    victim_country: str
    offender_country: str
    accepted_at: datetime
    entities: Mapping[str, Sequence[str]] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    dataset: str = "dossier-pilot"
    review_id: str | None = None
    notes: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PilotCaseSpec":
        """Instantiate a :class:`PilotCaseSpec` from a JSON payload."""

        return cls(
            case_id=str(payload["case_id"]),
            text=str(payload.get("text", "")),
            classification=str(payload.get("classification", "unknown")),
            confidence=float(payload.get("confidence", 0.0)),
            loss_amount_usd=_decimal_from(payload.get("loss_amount_usd")),
            jurisdiction=str(payload.get("jurisdiction", "unknown")),
            victim_country=str(payload.get("victim_country", "unknown")).upper(),
            offender_country=str(payload.get("offender_country", "unknown")).upper(),
            accepted_at=_parse_datetime(payload.get("accepted_at")),
            entities=_normalize_mapping(payload.get("entities") or {}),
            metadata=_normalize_mapping(payload.get("metadata") or {}),
            dataset=str(payload.get("dataset") or "dossier-pilot"),
            review_id=payload.get("review_id"),
            notes=payload.get("notes"),
        )


@dataclass(frozen=True)
class PilotSeedSummary:
    """Result describing structured + queue records written for pilot cases."""

    case_ids: Sequence[str]
    review_ids: Sequence[str]


@dataclass(frozen=True)
class PilotScheduleSummary:
    """Result describing queued pilot dossier plans."""

    plan_ids: Sequence[str]
    missing_cases: Sequence[str]
    case_ids: Sequence[str]
    dry_run: bool


def load_pilot_case_specs(path: str | Path | None = None) -> Sequence[PilotCaseSpec]:
    """Load pilot case specifications from disk."""

    resolved = Path(path) if path else DEFAULT_PILOT_CASES_PATH
    if not resolved.exists():
        raise FileNotFoundError(f"Pilot case config not found at {resolved}")
    payload = json.loads(resolved.read_text())
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("Pilot cases file must contain a JSON array of cases")
    return tuple(PilotCaseSpec.from_dict(item) for item in payload)


def seed_pilot_cases(
    specs: Sequence[PilotCaseSpec],
    *,
    review_store: ReviewStore | None = None,
    structured_store: StructuredStore | None = None,
) -> PilotSeedSummary:
    """Persist structured + queue records for pilot cases."""

    if not specs:
        return PilotSeedSummary(case_ids=tuple(), review_ids=tuple())

    review = review_store or build_review_store()
    structured = structured_store or build_structured_store()
    created_review_ids: list[str] = []

    try:
        for spec in specs:
            metadata = dict(spec.metadata)
            metadata.setdefault("dataset", spec.dataset)
            metadata.setdefault("loss_amount_usd", float(spec.loss_amount_usd))
            metadata.setdefault("jurisdiction", spec.jurisdiction)
            metadata.setdefault("victim_country", spec.victim_country)
            metadata.setdefault("offender_country", spec.offender_country)
            metadata.setdefault("jurisdiction_country", spec.victim_country)
            metadata.setdefault("cross_border", int(spec.victim_country != spec.offender_country))

            record = ScamRecord(
                case_id=spec.case_id,
                text=spec.text,
                entities={key: list(values) for key, values in spec.entities.items()},
                classification=spec.classification,
                confidence=spec.confidence,
                created_at=spec.accepted_at,
                metadata=metadata,
            )
            structured.upsert_record(record)

            review_id = review.upsert_queue_entry(
                review_id=spec.review_id,
                case_id=spec.case_id,
                status="accepted",
                queued_at=spec.accepted_at,
                last_updated=spec.accepted_at,
                priority="pilot",
                notes=spec.notes or "Pilot dossier candidate",
            )
            created_review_ids.append(review_id)
    finally:
        if structured_store is None and hasattr(structured, "close"):
            structured.close()

    return PilotSeedSummary(case_ids=tuple(spec.case_id for spec in specs), review_ids=tuple(created_review_ids))


def schedule_pilot_plans(
    specs: Sequence[PilotCaseSpec],
    *,
    bundle_builder: BundleBuilder | None = None,
    candidate_provider: BundleCandidateProvider | None = None,
    criteria: BundleCriteria | None = None,
    dry_run: bool = False,
) -> PilotScheduleSummary:
    """Generate and optionally enqueue dossier plans for the provided pilot cases."""

    if not specs:
        return PilotScheduleSummary(plan_ids=tuple(), missing_cases=tuple(), case_ids=tuple(), dry_run=dry_run)

    builder = bundle_builder or build_bundle_builder()
    provider = candidate_provider or build_bundle_candidate_provider()
    target_ids = {spec.case_id for spec in specs}
    candidates = [candidate for candidate in provider.list_candidates(limit=500) if candidate.case_id in target_ids]
    missing = sorted(target_ids - {candidate.case_id for candidate in candidates})

    if criteria is None:
        settings = get_settings()
        criteria = BundleCriteria(
            min_loss_usd=Decimal(str(settings.report.min_loss_usd)),
            recency_days=settings.report.recency_days,
            max_cases_per_dossier=settings.report.max_cases_per_dossier,
            jurisdiction_mode="single",
            require_cross_border=settings.report.require_cross_border,
        )

    if not candidates:
        return PilotScheduleSummary(
            plan_ids=tuple(), missing_cases=tuple(missing), case_ids=tuple(target_ids), dry_run=dry_run
        )

    if dry_run:
        plans = builder.generate_plans(candidates=candidates, criteria=criteria)
        return PilotScheduleSummary(
            plan_ids=tuple(plan.plan_id for plan in plans),
            missing_cases=tuple(missing),
            case_ids=tuple(target_ids),
            dry_run=True,
        )

    plan_ids = builder.build_and_enqueue(candidates=candidates, criteria=criteria)
    return PilotScheduleSummary(
        plan_ids=tuple(plan_ids),
        missing_cases=tuple(missing),
        case_ids=tuple(target_ids),
        dry_run=False,
    )


def _normalize_mapping(value: Any) -> Mapping[str, Sequence[str]]:
    if isinstance(value, Mapping):
        return {str(key): list(_coerce_sequence(items)) for key, items in value.items()}
    return {}


def _coerce_sequence(value: Any) -> Sequence[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _decimal_from(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


__all__ = [
    "PilotCaseSpec",
    "PilotSeedSummary",
    "PilotScheduleSummary",
    "DEFAULT_PILOT_CASES_PATH",
    "load_pilot_case_specs",
    "seed_pilot_cases",
    "schedule_pilot_plans",
]
