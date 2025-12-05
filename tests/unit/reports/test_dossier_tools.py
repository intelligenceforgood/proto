"""Tests for the LangChain dossier tool suite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from langchain_core.tools import BaseTool

from i4g.reports.bundle_builder import DossierCandidate, DossierPlan
from i4g.reports.dossier_analysis import analyze_plan
from i4g.reports.dossier_context import CaseContext, DossierContextResult
from i4g.reports.dossier_tools import DossierToolInput, DossierToolSuite
from i4g.reports.dossier_visuals import DossierVisualAssets


def _sample_plan() -> DossierPlan:
    accepted_at = datetime(2025, 12, 3, tzinfo=timezone.utc)
    candidate = DossierCandidate(
        case_id="case-1",
        loss_amount_usd=Decimal("125000"),
        accepted_at=accepted_at,
        jurisdiction="US-CA",
        cross_border=True,
        primary_entities=("wallet:1", "telegram:@suspect"),
    )
    return DossierPlan(
        plan_id="dossier-us-ca-20251203-01",
        jurisdiction_key="US-CA",
        created_at=accepted_at,
        total_loss_usd=candidate.loss_amount_usd,
        cases=[candidate],
        bundle_reason="test-bundle",
        cross_border=True,
        shared_drive_parent_id=None,
    )


def _context(case_id: str) -> DossierContextResult:
    case_ctx = CaseContext(
        case_id=case_id,
        structured_record={"case_id": case_id, "text": "case body"},
        review={"summary": "review summary"},
        warnings=("missing-attachments",),
    )
    return DossierContextResult(cases=(case_ctx,), warnings=case_ctx.warnings)


def test_tool_suite_produces_all_outputs(tmp_path) -> None:
    plan = _sample_plan()
    analysis = analyze_plan(plan)
    context = _context(plan.cases[0].case_id)
    chart = tmp_path / "timeline.png"
    chart.write_text("chart")
    geojson = tmp_path / "map.geojson"
    geojson.write_text("{}")
    geo_map = tmp_path / "map.png"
    geo_map.write_text("map")
    assets = DossierVisualAssets(
        timeline_chart=chart,
        geojson_path=geojson,
        geo_map_image=geo_map,
        warnings=("geo-warning",),
    )

    suite = DossierToolSuite()

    results = suite.run(plan=plan, context=context, analysis=analysis, assets=assets, asset_base=tmp_path)

    assert results.warnings == ()
    assert results.errors == {}
    assert {"geo_reasoner", "timeline_synthesizer", "entity_graph", "chart_renderer", "narrative_report"} <= set(
        results.outputs
    )
    chart_payload = results.outputs["chart_renderer"]
    assert chart_payload["timeline_chart"] == "timeline.png"
    narrative_payload = results.outputs["narrative_report"]
    assert "summary" in narrative_payload


class _FailingTool(BaseTool):
    name: str = "failing_tool"
    description: str = "Always raises to exercise warning paths."
    args_schema: type[DossierToolInput] = DossierToolInput

    def _run(self, plan: DossierToolInput) -> str:  # noqa: D401 - test stub
        raise RuntimeError("tool boom")

    async def _arun(self, *args, **kwargs):  # pragma: no cover - async not used
        raise NotImplementedError


def test_tool_suite_surfaces_tool_errors(tmp_path) -> None:
    plan = _sample_plan()
    analysis = analyze_plan(plan)
    context = _context(plan.cases[0].case_id)
    suite = DossierToolSuite(tools=[_FailingTool()])

    results = suite.run(plan=plan, context=context, analysis=analysis, assets=None, asset_base=tmp_path)

    assert results.errors == {"failing_tool": "tool boom"}
    assert results.warnings == ("failing_tool failed: tool boom",)
    assert results.outputs == {}
