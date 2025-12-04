"""Prototype renderers that generate dossier-ready visual assets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from i4g.reports.bundle_builder import DossierPlan
from i4g.settings import get_settings

# Basic centroid lookups for common jurisdictions and countries used in smoke data.
_COORDINATES: Mapping[str, tuple[float, float]] = {
    "GLOBAL": (0.0, 0.0),
    "US": (39.50, -98.35),
    "US-CA": (36.77, -119.42),
    "US-NY": (42.95, -75.53),
    "US-TX": (31.00, -99.00),
    "US-FL": (27.77, -81.69),
    "US-WA": (47.40, -121.49),
    "US-IL": (40.63, -89.39),
    "US-NJ": (40.15, -74.70),
    "CA": (56.13, -106.35),
    "MX": (23.63, -102.55),
    "GB": (55.38, -3.44),
    "IE": (53.41, -8.24),
    "AU": (-25.27, 133.77),
    "NZ": (-41.61, 172.82),
    "NG": (9.08, 8.68),
    "ZA": (-30.56, 22.94),
    "BR": (-14.24, -51.93),
    "PH": (12.88, 121.77),
    "IN": (20.59, 78.96),
    "CN": (35.86, 104.19),
    "JP": (36.20, 138.25),
}


@dataclass(frozen=True)
class TimelineChartResult:
    """Return value emitted by :class:`LossTimelineRenderer`."""

    image_path: Path | None
    warnings: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class GeoMapResult:
    """Container describing generated geo map assets."""

    geojson_path: Path | None
    image_path: Path | None
    warnings: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class DossierVisualAssets:
    """Aggregated outputs returned by :class:`DossierVisualBuilder`."""

    timeline_chart: Path | None
    geojson_path: Path | None
    geo_map_image: Path | None
    warnings: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        """Return a JSON-serializable payload describing generated assets."""

        return {
            "timeline_chart": str(self.timeline_chart) if self.timeline_chart else None,
            "geojson": str(self.geojson_path) if self.geojson_path else None,
            "geo_map_image": str(self.geo_map_image) if self.geo_map_image else None,
            "warnings": list(self.warnings),
        }


class LossTimelineRenderer:
    """Produces a simple bar chart summarizing per-case losses."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._font = ImageFont.load_default()

    def render(self, plan: DossierPlan) -> TimelineChartResult:
        """Render a loss timeline chart for the provided plan."""

        if not plan.cases:
            return TimelineChartResult(
                image_path=None,
                warnings=("No cases available for loss timeline chart",),
            )

        ordered = sorted(plan.cases, key=lambda case: case.accepted_at)
        max_loss = max((case.loss_amount_usd for case in ordered), default=Decimal("0"))
        chart_height = 420
        chart_width = 900
        margin = 60
        usable_height = chart_height - (margin * 2)
        usable_width = chart_width - (margin * 2)
        image = Image.new("RGB", (chart_width, chart_height), "white")
        draw = ImageDraw.Draw(image)

        # Axes
        draw.line((margin, margin, margin, chart_height - margin), fill="#1f2933", width=2)
        draw.line((margin, chart_height - margin, chart_width - margin, chart_height - margin), fill="#1f2933", width=2)
        draw.text((margin, 15), "Loss per accepted case (USD)", font=self._font, fill="#111")

        if max_loss <= 0:
            return TimelineChartResult(
                image_path=None,
                warnings=("Loss timeline chart skipped because all cases have zero reported loss",),
            )

        bar_width = usable_width / len(ordered)
        baseline = chart_height - margin
        for index, case in enumerate(ordered):
            loss_value = max(case.loss_amount_usd, Decimal("0"))
            height_ratio = float(loss_value / max_loss)
            bar_height = height_ratio * usable_height
            x0 = margin + index * bar_width + (bar_width * 0.15)
            x1 = x0 + (bar_width * 0.7)
            y1 = baseline
            y0 = baseline - bar_height
            draw.rectangle((x0, y0, x1, y1), fill="#ff6b35")
            label = _format_label(case.accepted_at)
            draw.text((x0 - 5, baseline + 8), label, font=self._font, fill="#4b5563")
            draw.text((x0, y0 - 14), f"${int(loss_value):,}", font=self._font, fill="#111")

        output_path = self._output_dir / f"{plan.plan_id}_loss_timeline.png"
        image.save(output_path, format="PNG")
        return TimelineChartResult(image_path=output_path)


class GeoMapRenderer:
    """Renders approximate geographic scatter plots and GeoJSON payloads."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._font = ImageFont.load_default()

    def render(self, plan: DossierPlan) -> GeoMapResult:
        """Generate a GeoJSON feature collection and preview map for ``plan``."""

        if not plan.cases:
            return GeoMapResult(
                geojson_path=None,
                image_path=None,
                warnings=("Geo map skipped because dossier plan has no cases",),
            )

        features: list[dict] = []
        mapped_cases: list[tuple[float, float, str, Decimal, bool]] = []
        warnings: list[str] = []
        for candidate in plan.cases:
            coord = self._resolve_coordinates(candidate.jurisdiction)
            if not coord:
                warnings.append(f"No coordinates available for jurisdiction {candidate.jurisdiction or 'unknown'}")
                continue
            lon, lat = coord
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "case_id": candidate.case_id,
                        "jurisdiction": candidate.jurisdiction,
                        "loss_amount_usd": str(candidate.loss_amount_usd),
                        "cross_border": candidate.cross_border,
                    },
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                }
            )
            mapped_cases.append(
                (lon, lat, candidate.jurisdiction or "unknown", candidate.loss_amount_usd, candidate.cross_border)
            )

        geojson_path: Path | None = None
        map_path: Path | None = None

        if features:
            geojson = {"type": "FeatureCollection", "features": features}
            geojson_path = self._output_dir / f"{plan.plan_id}_geo.json"
            geojson_path.write_text(json.dumps(geojson, indent=2))
            map_path = self._render_scatter_plot(plan.plan_id, mapped_cases)
        else:
            warnings.append("Geo map skipped because no case coordinates were resolved")

        return GeoMapResult(geojson_path=geojson_path, image_path=map_path, warnings=tuple(warnings))

    def _render_scatter_plot(
        self,
        plan_id: str,
        mapped_cases: Iterable[tuple[float, float, str, Decimal, bool]],
    ) -> Path:
        width = 960
        height = 480
        margin = 40
        image = Image.new("RGB", (width, height), "#041c32")
        draw = ImageDraw.Draw(image)

        # Grid lines for visual context
        for lon in range(-120, 181, 60):
            x = _project_x(lon, width)
            draw.line((x, 0, x, height), fill="#0f2d44")
        for lat in range(-60, 91, 30):
            y = _project_y(lat, height)
            draw.line((0, y, width, y), fill="#0f2d44")

        draw.text((margin, 15), "Approximate case locations", font=self._font, fill="#ffffff")
        legend_y = height - margin
        draw.text((margin, legend_y - 15), "● Jurisdiction match", font=self._font, fill="#3dd598")
        draw.text((margin + 220, legend_y - 15), "● Cross-border", font=self._font, fill="#ffd166")

        for lon, lat, jurisdiction, loss, cross_border in mapped_cases:
            x = _project_x(lon, width)
            y = _project_y(lat, height)
            radius = 7
            color = "#ffd166" if cross_border else "#3dd598"
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color, outline="#02111b")
            draw.text((x + 8, y - 6), f"{jurisdiction} (${int(loss):,})", font=self._font, fill="#e5e7eb")

        output_path = self._output_dir / f"{plan_id}_geo_map.png"
        image.save(output_path, format="PNG")
        return output_path

    def _resolve_coordinates(self, jurisdiction: str | None) -> tuple[float, float] | None:
        if not jurisdiction:
            return None
        key = jurisdiction.upper()
        if key in _COORDINATES:
            return _COORDINATES[key]
        if "-" in key:
            region = key.split("-", 1)[0]
            return _COORDINATES.get(region)
        return _COORDINATES.get(key)


class DossierVisualBuilder:
    """High-level helper that orchestrates visual renderers."""

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        timeline_renderer: LossTimelineRenderer | None = None,
        geo_renderer: GeoMapRenderer | None = None,
    ) -> None:
        settings = get_settings()
        reports_dir = base_dir or (settings.data_dir / "reports" / "dossiers")
        assets_dir = reports_dir / "assets"
        charts_dir = assets_dir / "charts"
        geo_dir = assets_dir / "geo"
        self._timeline_renderer = timeline_renderer or LossTimelineRenderer(charts_dir)
        self._geo_renderer = geo_renderer or GeoMapRenderer(geo_dir)

    def render(self, plan: DossierPlan) -> DossierVisualAssets:
        """Generate every configured visual asset for ``plan``."""

        timeline_result = self._timeline_renderer.render(plan)
        geo_result = self._geo_renderer.render(plan)

        combined_warnings = list(timeline_result.warnings) + list(geo_result.warnings)
        return DossierVisualAssets(
            timeline_chart=timeline_result.image_path,
            geojson_path=geo_result.geojson_path,
            geo_map_image=geo_result.image_path,
            warnings=tuple(combined_warnings),
        )


def _format_label(value: datetime) -> str:
    if not value:
        return ""
    return value.strftime("%m-%d")


def _project_x(lon: float, width: int) -> float:
    return (lon + 180.0) / 360.0 * width


def _project_y(lat: float, height: int) -> float:
    return (90.0 - lat) / 180.0 * height


__all__ = [
    "DossierVisualAssets",
    "DossierVisualBuilder",
    "GeoMapRenderer",
    "GeoMapResult",
    "LossTimelineRenderer",
    "TimelineChartResult",
]
