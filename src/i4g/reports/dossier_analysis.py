"""Analytical helpers that summarize dossier plans for downstream tooling."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Sequence

from i4g.reports.bundle_builder import DossierPlan


@dataclass(frozen=True)
class DossierAnalysis:
    """Aggregated statistics derived from a :class:`DossierPlan`."""

    case_count: int
    total_loss_usd: Decimal
    loss_by_jurisdiction: Dict[str, Decimal]
    cross_border_cases: Sequence[str]
    accepted_range: tuple[datetime | None, datetime | None]
    primary_entity_frequencies: Sequence[tuple[str, int]]

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of the analysis payload."""

        earliest, latest = self.accepted_range
        return {
            "case_count": self.case_count,
            "total_loss_usd": str(self.total_loss_usd),
            "loss_by_jurisdiction": {key: str(value) for key, value in self.loss_by_jurisdiction.items()},
            "cross_border_cases": list(self.cross_border_cases),
            "accepted_range": {
                "earliest": earliest.isoformat() if earliest else None,
                "latest": latest.isoformat() if latest else None,
            },
            "primary_entity_frequencies": [
                {"entity": entity, "count": count} for entity, count in self.primary_entity_frequencies
            ],
        }


def analyze_plan(plan: DossierPlan, *, top_entities: int = 10) -> DossierAnalysis:
    """Compute aggregate statistics for ``plan`` to enrich dossier manifests."""

    loss_by_jurisdiction: Dict[str, Decimal] = {}
    cross_border_cases: List[str] = []
    entity_counter: Counter[str] = Counter()
    accepted_values: List[datetime] = []

    for candidate in plan.cases:
        jurisdiction = (candidate.jurisdiction or "unknown").strip() or "unknown"
        loss_by_jurisdiction[jurisdiction] = (
            loss_by_jurisdiction.get(jurisdiction, Decimal("0")) + candidate.loss_amount_usd
        )
        if candidate.cross_border:
            cross_border_cases.append(candidate.case_id)
        accepted_values.append(candidate.accepted_at)
        for entity in candidate.primary_entities:
            normalized = str(entity).strip()
            if normalized:
                entity_counter[normalized] += 1

    earliest = min(accepted_values) if accepted_values else None
    latest = max(accepted_values) if accepted_values else None
    frequency_pairs = sorted(
        entity_counter.items(),
        key=lambda item: (-item[1], item[0]),
    )[:top_entities]

    return DossierAnalysis(
        case_count=len(plan.cases),
        total_loss_usd=plan.total_loss_usd,
        loss_by_jurisdiction=loss_by_jurisdiction,
        cross_border_cases=tuple(cross_border_cases),
        accepted_range=(earliest, latest),
        primary_entity_frequencies=tuple(frequency_pairs),
    )


__all__ = ["DossierAnalysis", "analyze_plan"]
