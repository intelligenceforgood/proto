"""Bundle builder models and orchestration helpers for Milestone 4 dossiers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, List, Literal, Optional, Sequence

from i4g.store.dossier_queue_store import DossierQueueStore

JurisdictionMode = Literal["single", "multi", "global"]


@dataclass(frozen=True)
class DossierCandidate:
    """Represents a single accepted case that may be bundled into a dossier."""

    case_id: str
    loss_amount_usd: Decimal
    accepted_at: datetime
    jurisdiction: str
    cross_border: bool = False
    primary_entities: Sequence[str] = field(default_factory=list)

    def is_recent(self, *, recency_days: int, reference_time: datetime) -> bool:
        """Return True when the candidate was accepted within the rolling window."""

        cutoff = reference_time - timedelta(days=recency_days)
        return self.accepted_at >= cutoff


@dataclass(frozen=True)
class BundleCriteria:
    """Filtering knobs that determine when cases qualify for dossier bundling."""

    min_loss_usd: Decimal = Decimal("50000")
    recency_days: int = 30
    max_cases_per_dossier: int = 5
    jurisdiction_mode: JurisdictionMode = "single"
    require_cross_border: bool = False


@dataclass(frozen=True)
class DossierPlan:
    """Serializable dossier blueprint queued for downstream agent execution."""

    plan_id: str
    jurisdiction_key: str
    created_at: datetime
    total_loss_usd: Decimal
    cases: List[DossierCandidate]
    bundle_reason: str
    cross_border: bool
    shared_drive_parent_id: Optional[str] = None

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation of the plan."""

        return {
            "plan_id": self.plan_id,
            "jurisdiction_key": self.jurisdiction_key,
            "created_at": self.created_at.isoformat(),
            "total_loss_usd": str(self.total_loss_usd),
            "bundle_reason": self.bundle_reason,
            "cross_border": self.cross_border,
            "shared_drive_parent_id": self.shared_drive_parent_id,
            "cases": [
                {
                    "case_id": case.case_id,
                    "loss_amount_usd": str(case.loss_amount_usd),
                    "accepted_at": case.accepted_at.isoformat(),
                    "jurisdiction": case.jurisdiction,
                    "cross_border": case.cross_border,
                    "primary_entities": list(case.primary_entities),
                }
                for case in self.cases
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "DossierPlan":
        """Instantiate a plan from the serialized payload."""

        cases = [
            DossierCandidate(
                case_id=item["case_id"],
                loss_amount_usd=Decimal(item["loss_amount_usd"]),
                accepted_at=datetime.fromisoformat(item["accepted_at"]),
                jurisdiction=item["jurisdiction"],
                cross_border=bool(item.get("cross_border", False)),
                primary_entities=tuple(item.get("primary_entities") or []),
            )
            for item in payload.get("cases", [])
        ]
        return cls(
            plan_id=payload["plan_id"],
            jurisdiction_key=payload["jurisdiction_key"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            total_loss_usd=Decimal(payload["total_loss_usd"]),
            cases=cases,
            bundle_reason=payload["bundle_reason"],
            cross_border=bool(payload.get("cross_border", False)),
            shared_drive_parent_id=payload.get("shared_drive_parent_id"),
        )


class BundleBuilder:
    """Groups accepted cases into deterministic bundles and enqueues dossier plans."""

    def __init__(
        self,
        queue_store: DossierQueueStore,
        *,
        shared_drive_parent_id: Optional[str] = None,
    ) -> None:
        self._queue_store = queue_store
        self._shared_drive_parent_id = shared_drive_parent_id

    def build_and_enqueue(
        self,
        candidates: Iterable[DossierCandidate],
        criteria: BundleCriteria,
    ) -> List[str]:
        """Build dossier plans from candidates and enqueue them.

        Args:
            candidates: Iterable of accepted cases filtered upstream.
            criteria: Bundling thresholds and grouping mode.

        Returns:
            List of plan IDs that were successfully enqueued.
        """

        plans = self.generate_plans(candidates=candidates, criteria=criteria)
        enqueued: List[str] = []
        for plan in plans:
            self._queue_store.enqueue_plan(plan)
            enqueued.append(plan.plan_id)
        return enqueued

    def generate_plans(
        self,
        candidates: Iterable[DossierCandidate],
        criteria: BundleCriteria,
        *,
        reference_time: Optional[datetime] = None,
    ) -> List[DossierPlan]:
        """Return deterministic bundles without mutating queue state."""

        now = reference_time or datetime.now(timezone.utc)
        filtered = self._filter_candidates(candidates, criteria, now)
        buckets = self._group_candidates(filtered, criteria)
        plans: List[DossierPlan] = []
        for bucket_key, bucket_candidates in buckets.items():
            chunks = self._chunk(bucket_candidates, criteria.max_cases_per_dossier)
            for index, chunk in enumerate(chunks, start=1):
                plan = self._build_plan(
                    chunk,
                    bucket_key=bucket_key,
                    chunk_index=index,
                    reference_time=now,
                )
                plans.append(plan)
        return plans

    def _filter_candidates(
        self,
        candidates: Iterable[DossierCandidate],
        criteria: BundleCriteria,
        reference_time: datetime,
    ) -> List[DossierCandidate]:
        """Apply loss, recency, and cross-border filters."""

        result: List[DossierCandidate] = []
        for candidate in candidates:
            if candidate.loss_amount_usd < criteria.min_loss_usd:
                continue
            if not candidate.is_recent(recency_days=criteria.recency_days, reference_time=reference_time):
                continue
            if criteria.require_cross_border and not candidate.cross_border:
                continue
            result.append(candidate)
        return result

    def _group_candidates(
        self,
        candidates: Sequence[DossierCandidate],
        criteria: BundleCriteria,
    ) -> dict[str, List[DossierCandidate]]:
        """Group candidates according to the jurisdiction mode."""

        buckets: dict[str, List[DossierCandidate]] = {}
        if criteria.jurisdiction_mode == "global":
            buckets["global"] = list(candidates)
            return buckets

        for candidate in candidates:
            if criteria.jurisdiction_mode == "multi" and candidate.cross_border:
                key = "cross-border"
            else:
                key = candidate.jurisdiction or "unknown"
            buckets.setdefault(key, []).append(candidate)
        return buckets

    def _chunk(
        self,
        candidates: Sequence[DossierCandidate],
        chunk_size: int,
    ) -> List[List[DossierCandidate]]:
        """Yield deterministic slices bounded by chunk_size."""

        if chunk_size <= 0:
            return [list(candidates)]
        return [list(candidates[i : i + chunk_size]) for i in range(0, len(candidates), chunk_size)]

    def _build_plan(
        self,
        candidates: Sequence[DossierCandidate],
        *,
        bucket_key: str,
        chunk_index: int,
        reference_time: datetime,
    ) -> DossierPlan:
        """Construct a DossierPlan payload for the chunk."""

        total_loss = sum((case.loss_amount_usd for case in candidates), Decimal("0"))
        plan_id = self._build_plan_id(bucket_key=bucket_key, reference_time=reference_time, chunk_index=chunk_index)
        reason = self._derive_bundle_reason(bucket_key=bucket_key, candidates=candidates)
        cross_border = any(candidate.cross_border for candidate in candidates)
        return DossierPlan(
            plan_id=plan_id,
            jurisdiction_key=bucket_key,
            created_at=reference_time,
            total_loss_usd=total_loss,
            cases=list(candidates),
            bundle_reason=reason,
            cross_border=cross_border,
            shared_drive_parent_id=self._shared_drive_parent_id,
        )

    def _build_plan_id(
        self,
        *,
        bucket_key: str,
        reference_time: datetime,
        chunk_index: int,
    ) -> str:
        """Create a deterministic identifier for queueing."""

        safe_key = bucket_key.lower().replace(" ", "-") or "unknown"
        timestamp = reference_time.strftime("%Y%m%d")
        return f"dossier-{safe_key}-{timestamp}-{chunk_index:02d}"

    def _derive_bundle_reason(
        self,
        *,
        bucket_key: str,
        candidates: Sequence[DossierCandidate],
    ) -> str:
        """Return a human-readable explanation for the grouping."""

        if bucket_key == "global":
            return "Global dossier: high-loss cases aggregated across jurisdictions"
        if bucket_key == "cross-border":
            return "Cross-border dossier: mixed jurisdictions sharing cross-border indicators"
        if len(candidates) == 1:
            return f"Single jurisdiction ({bucket_key}) dossier"
        return f"{bucket_key} dossier ({len(candidates)} cases, shared entities)"
