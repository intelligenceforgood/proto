"""Context loading helpers for dossier generation pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence

from i4g.reports.bundle_builder import DossierPlan
from i4g.store.review_store import ReviewStore
from i4g.store.schema import ScamRecord
from i4g.store.structured import StructuredStore


@dataclass(frozen=True)
class CaseContext:
    """Serializable per-case context payload used by dossier generation."""

    case_id: str
    structured_record: Dict[str, Any] | None
    review: Dict[str, Any] | None
    warnings: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dictionary for downstream usage."""

        return {
            "case_id": self.case_id,
            "structured_record": self.structured_record,
            "review": self.review,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class DossierContextResult:
    """Aggregated context payload returned by :class:`DossierContextLoader`."""

    cases: Sequence[CaseContext]
    warnings: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of the context result."""

        return {
            "cases": [case.to_dict() for case in self.cases],
            "warnings": list(self.warnings),
        }


class DossierContextLoader:
    """Fetches structured and review metadata for cases referenced by a plan."""

    def __init__(
        self,
        *,
        structured_store: StructuredStore,
        review_store: ReviewStore,
    ) -> None:
        self._structured_store = structured_store
        self._review_store = review_store

    def load(self, plan: DossierPlan) -> DossierContextResult:
        """Return contextual metadata for every case associated with ``plan``."""

        case_ids = _unique_case_ids(plan)
        if not case_ids:
            return DossierContextResult(cases=tuple(), warnings=tuple())

        review_map = self._review_store.get_cases(case_ids)
        cases: List[CaseContext] = []
        aggregated_warnings: List[str] = []

        for case_id in case_ids:
            structured = self._structured_store.get_by_id(case_id)
            review = review_map.get(case_id)
            warnings = _case_warnings(structured=structured, review=review, case_id=case_id)
            aggregated_warnings.extend(warnings)
            cases.append(
                CaseContext(
                    case_id=case_id,
                    structured_record=structured.to_dict() if structured else None,
                    review=review,
                    warnings=tuple(warnings),
                )
            )

        return DossierContextResult(cases=tuple(cases), warnings=tuple(_deduplicate(aggregated_warnings)))


def _unique_case_ids(plan: DossierPlan) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for candidate in plan.cases:
        case_id = (candidate.case_id or "").strip()
        if not case_id or case_id in seen:
            continue
        seen.add(case_id)
        ordered.append(case_id)
    return ordered


def _case_warnings(*, structured: ScamRecord | None, review: Dict[str, Any] | None, case_id: str) -> List[str]:
    warnings: List[str] = []
    if structured is None:
        warnings.append(f"No structured record found for case {case_id}")
    if review is None:
        warnings.append(f"No review metadata found for case {case_id}")
    return warnings


def _deduplicate(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


__all__ = [
    "CaseContext",
    "DossierContextResult",
    "DossierContextLoader",
]
