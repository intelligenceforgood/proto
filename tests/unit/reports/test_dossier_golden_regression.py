"""Golden-sample regression tests for dossier artifact generation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Sequence

from i4g.reports.bundle_builder import DossierCandidate, DossierPlan
from i4g.reports.dossier_analysis import DossierAnalysis
from i4g.reports.dossier_context import CaseContext, DossierContextResult
from i4g.reports.dossier_pipeline import DossierGenerator
from i4g.reports.dossier_templates import TemplateRegistry
from i4g.reports.dossier_tools import DossierToolResults
from i4g.reports.dossier_visuals import DossierVisualAssets

GOLDEN_TIMESTAMP = datetime(2025, 12, 4, tzinfo=timezone.utc)

# Filled after first deterministic render; keep values in sync when templates or tools change.
EXPECTED_HASHES = {
    "manifest": "ee1c4d526d3eeea841516f731825ac558ae41daa22863d23f394a25006595180",
    "markdown": "fa9df598c159df8fdc9c97558a1353cbbe86e9cf9c1f9acba97c006177ff171a",
    "signatures": "0fc6ed1f4d9d6156f2a9c0f915d3601f6a333e84e75fe395ef5f17d1e547fbc2",
}


def test_dossier_golden_sample_regression(tmp_path) -> None:
    artifact_dir = tmp_path / "artifacts"
    generator = DossierGenerator(
        artifact_dir=artifact_dir,
        context_loader=_GoldenContextLoader(),
        visuals_builder=_GoldenVisualBuilder(artifact_dir),
        tool_suite=_GoldenToolSuite(),
        template_registry=TemplateRegistry(),
        now_provider=lambda: GOLDEN_TIMESTAMP,
    )
    plan = _golden_plan()

    generator.generate_from_plan(plan)

    manifest_path = artifact_dir / f"{plan.plan_id}.json"
    markdown_path = artifact_dir / f"{plan.plan_id}.md"
    signature_path = artifact_dir / f"{plan.plan_id}.signatures.json"
    actual_hashes = {
        "manifest": _sha256(manifest_path),
        "markdown": _sha256(markdown_path),
        "signatures": _sha256(signature_path),
    }
    assert actual_hashes == EXPECTED_HASHES

    payload = json.loads(manifest_path.read_text())
    assert payload["plan_id"] == plan.plan_id
    assert payload["case_count"] == 2
    assert payload["template_render"]["rendered_parts"] == ["cover", "analysis", "timeline", "entities", "appendix"]
    tools = payload["tools"]["outputs"]
    assert tools["narrative_report"]["risk_level"] == "elevated"
    assert tools["timeline_synthesizer"]["events"][0]["case_id"] == "case-1"
    chart_renderer = tools["chart_renderer"]
    assert chart_renderer["timeline_chart"].endswith("timeline.png")
    assert payload["signature_manifest"]["path"].endswith(".signatures.json")


def _golden_plan() -> DossierPlan:
    accepted = datetime(2025, 12, 1, tzinfo=timezone.utc)
    cases = [
        DossierCandidate(
            case_id="case-1",
            loss_amount_usd=Decimal("125000"),
            accepted_at=accepted,
            jurisdiction="US-CA",
            cross_border=True,
            primary_entities=("wallet:alpha", "telegram:@alpha"),
        ),
        DossierCandidate(
            case_id="case-2",
            loss_amount_usd=Decimal("150000"),
            accepted_at=accepted,
            jurisdiction="US-NY",
            cross_border=False,
            primary_entities=("wallet:beta", "telegram:@alpha"),
        ),
    ]
    return DossierPlan(
        plan_id="dossier-golden-20251204-01",
        jurisdiction_key="US-cross",
        created_at=datetime(2025, 12, 2, tzinfo=timezone.utc),
        total_loss_usd=sum(case.loss_amount_usd for case in cases),
        cases=cases,
        bundle_reason="Golden sample regression",
        cross_border=True,
        shared_drive_parent_id=None,
    )


class _GoldenContextLoader:
    def load(self, plan: DossierPlan) -> DossierContextResult:  # noqa: D401 - deterministic stub
        contexts: list[CaseContext] = []
        for candidate in plan.cases:
            contexts.append(
                CaseContext(
                    case_id=candidate.case_id,
                    structured_record={
                        "case_id": candidate.case_id,
                        "jurisdiction": candidate.jurisdiction,
                        "loss_amount_usd": str(candidate.loss_amount_usd),
                    },
                    review={
                        "review_id": f"review-{candidate.case_id}",
                        "summary": f"Summary for {candidate.case_id}",
                    },
                    warnings=tuple(),
                )
            )
        return DossierContextResult(cases=tuple(contexts), warnings=tuple())


class _GoldenVisualBuilder:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def render(self, plan: DossierPlan) -> DossierVisualAssets:  # noqa: D401 - deterministic stub
        assets_dir = self._base_dir / "assets"
        charts_dir = assets_dir / "charts"
        geo_dir = assets_dir / "geo"
        charts_dir.mkdir(parents=True, exist_ok=True)
        geo_dir.mkdir(parents=True, exist_ok=True)
        timeline_path = charts_dir / f"{plan.plan_id}_timeline.png"
        timeline_path.write_text("golden timeline chart")
        geojson_path = geo_dir / f"{plan.plan_id}.geojson"
        geojson_payload = {
            "plan_id": plan.plan_id,
            "cases": [candidate.case_id for candidate in plan.cases],
        }
        geojson_path.write_text(json.dumps(geojson_payload, indent=2))
        geo_map_image = geo_dir / f"{plan.plan_id}_map.png"
        geo_map_image.write_text("golden map image")
        return DossierVisualAssets(
            timeline_chart=timeline_path,
            geojson_path=geojson_path,
            geo_map_image=geo_map_image,
            warnings=tuple(),
        )


class _GoldenToolSuite:
    def run(
        self,
        *,
        plan: DossierPlan,
        context: DossierContextResult | None,
        analysis: DossierAnalysis,
        assets: DossierVisualAssets | None,
        asset_base: Path | None,
    ) -> DossierToolResults:  # noqa: D401 - deterministic stub
        asset_payload = assets.to_dict(relative_to=asset_base) if assets else {}
        geo_counts = _jurisdiction_counts(plan)
        events = [
            {
                "timestamp": candidate.accepted_at.isoformat(),
                "case_id": candidate.case_id,
                "summary": f"Loss USD {candidate.loss_amount_usd}",
                "loss_amount_usd": str(candidate.loss_amount_usd),
            }
            for candidate in plan.cases
        ]
        entity_map = _entity_map(plan)
        outputs = {
            "geo_reasoner": {
                "jurisdiction_counts": geo_counts,
                "primary_regions": list(geo_counts.keys()),
                "cross_border_cases": [candidate.case_id for candidate in plan.cases if candidate.cross_border],
                "warnings": [],
            },
            "timeline_synthesizer": {
                "events": events,
                "warnings": [],
            },
            "entity_graph": {
                "entities": entity_map,
                "entity_count": len(entity_map),
                "top_clusters": [{"entity": entity, "count": len(case_ids)} for entity, case_ids in entity_map.items()],
            },
            "chart_renderer": asset_payload,
            "narrative_report": {
                "summary": f"{len(plan.cases)} dossier cases totaling ${analysis.total_loss_usd}",
                "risk_level": "elevated",
                "recommendation": "Escalate to task force",
                "confidence": 0.95,
            },
        }
        return DossierToolResults(outputs=outputs, warnings=tuple(), errors={})


def _jurisdiction_counts(plan: DossierPlan) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for candidate in plan.cases:
        key = candidate.jurisdiction or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _entity_map(plan: DossierPlan) -> Dict[str, Sequence[str]]:
    adjacency: Dict[str, list[str]] = {}
    for candidate in plan.cases:
        for entity in candidate.primary_entities:
            adjacency.setdefault(entity, []).append(candidate.case_id)
    return {entity: tuple(case_ids) for entity, case_ids in adjacency.items()}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()
