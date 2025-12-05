"""Unit tests for dossier visual asset renderers."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from i4g.reports.bundle_builder import DossierCandidate, DossierPlan
from i4g.reports.dossier_visuals import DossierVisualBuilder, GeoMapRenderer, LossTimelineRenderer


def test_loss_timeline_renderer_outputs_chart(tmp_path) -> None:
    renderer = LossTimelineRenderer(output_dir=tmp_path)
    plan = _plan(jurisdictions=("US-CA", "US-NY"))

    result = renderer.render(plan)

    assert result.image_path is not None
    assert result.image_path.exists()
    assert result.warnings == ()


def test_geo_renderer_generates_geojson_and_map(tmp_path) -> None:
    renderer = GeoMapRenderer(output_dir=tmp_path)
    plan = _plan(jurisdictions=("US-CA", "ZZ-UNKNOWN"))

    result = renderer.render(plan)

    assert result.geojson_path is not None and result.geojson_path.exists()
    assert result.image_path is not None and result.image_path.exists()
    assert result.warnings == ("No coordinates available for jurisdiction ZZ-UNKNOWN",)


def test_visual_builder_combines_assets(tmp_path) -> None:
    builder = DossierVisualBuilder(base_dir=tmp_path)
    plan = _plan(jurisdictions=("US-CA", "US-NY"))

    assets = builder.render(plan)

    assert assets.timeline_chart and assets.timeline_chart.exists()
    assert assets.geo_map_image and assets.geo_map_image.exists()
    assert assets.geojson_path and assets.geojson_path.exists()
    snapshot = assets.to_dict()
    assert snapshot["timeline_chart"].endswith(".png")
    assert snapshot["geojson"].endswith(".json")
    relative_snapshot = assets.to_dict(relative_to=tmp_path)
    assert relative_snapshot["timeline_chart"].startswith("assets/")
    assert relative_snapshot["geojson"].startswith("assets/")


def _plan(jurisdictions: tuple[str, str]) -> DossierPlan:
    now = datetime(2025, 12, 3, tzinfo=timezone.utc)
    cases = []
    for index, jurisdiction in enumerate(jurisdictions, start=1):
        cases.append(
            DossierCandidate(
                case_id=f"case-{index}",
                loss_amount_usd=Decimal("50000") * index,
                accepted_at=now,
                jurisdiction=jurisdiction,
                cross_border=(jurisdiction == "US-NY"),
                primary_entities=(f"wallet:{index}",),
            )
        )
    return DossierPlan(
        plan_id="test-dossier",
        jurisdiction_key="test",
        created_at=now,
        total_loss_usd=sum(case.loss_amount_usd for case in cases),
        cases=cases,
        bundle_reason="unit-test",
        cross_border=True,
        shared_drive_parent_id=None,
    )
