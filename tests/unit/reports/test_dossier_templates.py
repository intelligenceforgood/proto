"""Tests for dossier template registry rendering."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from i4g.reports.bundle_builder import DossierCandidate, DossierPlan
from i4g.reports.dossier_analysis import analyze_plan
from i4g.reports.dossier_templates import TemplatePart, TemplateRegistry


def _plan() -> DossierPlan:
    accepted_at = datetime(2025, 12, 3, tzinfo=timezone.utc)
    candidate = DossierCandidate(
        case_id="case-1",
        loss_amount_usd=Decimal("150000"),
        accepted_at=accepted_at,
        jurisdiction="US-CA",
        cross_border=False,
        primary_entities=("wallet:xyz",),
    )
    return DossierPlan(
        plan_id="dossier-us-ca",
        jurisdiction_key="US-CA",
        created_at=accepted_at,
        total_loss_usd=candidate.loss_amount_usd,
        cases=[candidate],
        bundle_reason="unit test",
        cross_border=False,
        shared_drive_parent_id=None,
    )


def test_template_registry_renders_default_templates(tmp_path) -> None:
    registry = TemplateRegistry()
    plan = _plan()
    analysis = analyze_plan(plan)

    result = registry.render(
        destination=tmp_path / "dossier.md",
        plan=plan,
        analysis=analysis,
        context=None,
        tool_results=None,
        assets=None,
        asset_base=tmp_path,
    )

    assert result.path == tmp_path / "dossier.md"
    assert result.markdown.startswith("# Evidence Dossier")
    assert "## Analytical Snapshot" in result.markdown
    assert result.rendered_parts != ()
    assert result.warnings == ()


def test_template_registry_warns_when_required_template_missing(tmp_path) -> None:
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "cover.md.j2").write_text("Cover {{ plan.plan_id }}")
    registry = TemplateRegistry(
        template_dir=template_dir,
        parts=(
            TemplatePart(name="cover", template_name="cover.md.j2"),
            TemplatePart(name="analysis", template_name="analysis.md.j2", required=True),
        ),
    )
    plan = _plan()
    analysis = analyze_plan(plan)

    result = registry.render(
        destination=None,
        plan=plan,
        analysis=analysis,
        context=None,
        tool_results=None,
        assets=None,
        asset_base=tmp_path,
    )

    assert result.warnings == (f"Template 'analysis.md.j2' was not found in {template_dir}",)
    assert result.rendered_parts == ("cover",)
