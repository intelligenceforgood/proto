"""Template registry for composing dossier markdown artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from i4g.reports.bundle_builder import DossierPlan
from i4g.reports.dossier_analysis import DossierAnalysis
from i4g.reports.dossier_context import DossierContextResult
from i4g.reports.dossier_tools import DossierToolResults
from i4g.reports.dossier_visuals import DossierVisualAssets
from i4g.settings import PROJECT_ROOT


@dataclass(frozen=True)
class TemplatePart:
    """Metadata describing a renderable template fragment."""

    name: str
    template_name: str
    required: bool = True
    description: str | None = None


@dataclass(frozen=True)
class TemplateRenderResult:
    """Return value emitted when rendering dossier templates."""

    path: Path | None
    markdown: str
    warnings: Sequence[str] = field(default_factory=tuple)
    rendered_parts: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "path": str(self.path) if self.path else None,
            "warnings": list(self.warnings),
            "rendered_parts": list(self.rendered_parts),
        }


DEFAULT_TEMPLATE_PARTS: Sequence[TemplatePart] = (
    TemplatePart(name="cover", template_name="cover.md.j2"),
    TemplatePart(name="analysis", template_name="analysis.md.j2"),
    TemplatePart(name="timeline", template_name="timeline.md.j2", required=False),
    TemplatePart(name="entities", template_name="entities.md.j2", required=False),
    TemplatePart(name="appendix", template_name="appendix.md.j2"),
)


class TemplateRegistry:
    """Loads and renders dossier markdown templates from disk."""

    def __init__(
        self,
        *,
        template_dir: Path | None = None,
        parts: Sequence[TemplatePart] | None = None,
    ) -> None:
        self._template_dir = template_dir or (PROJECT_ROOT / "templates" / "reports" / "dossiers")
        self._environment = Environment(
            loader=FileSystemLoader(str(self._template_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._parts = tuple(parts or DEFAULT_TEMPLATE_PARTS)

    def render(
        self,
        *,
        destination: Path | None,
        plan: DossierPlan,
        analysis: DossierAnalysis,
        context: DossierContextResult | None,
        tool_results: DossierToolResults | None,
        assets: DossierVisualAssets | Mapping[str, Any] | None,
        asset_base: Path | None = None,
    ) -> TemplateRenderResult:
        """Render configured templates using the supplied context."""

        context_payload = {
            "plan": plan.to_dict(),
            "analysis": analysis.to_dict(),
            "context": context.to_dict() if context else None,
            "tools": tool_results.to_dict() if tool_results else None,
            "assets": _serialise_assets(assets, asset_base),
        }
        warnings: list[str] = []
        sections: list[str] = []
        rendered_part_names: list[str] = []
        for part in self._parts:
            try:
                template = self._environment.get_template(part.template_name)
            except TemplateNotFound:
                warning = f"Template '{part.template_name}' was not found in {self._template_dir}"
                warnings.append(warning)
                if part.required:
                    continue
                else:
                    continue
            rendered = template.render(**context_payload).strip()
            if not rendered:
                if part.required:
                    warnings.append(f"Template '{part.name}' produced empty output")
                continue
            sections.append(rendered)
            rendered_part_names.append(part.name)
        markdown = "\n\n".join(sections)
        output_path = destination
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown or "_Dossier template produced no content._\n")
        return TemplateRenderResult(
            path=output_path,
            markdown=markdown,
            warnings=tuple(warnings),
            rendered_parts=tuple(rendered_part_names),
        )


__all__ = [
    "TemplatePart",
    "TemplateRegistry",
    "TemplateRenderResult",
]


def _serialise_assets(
    assets: DossierVisualAssets | Mapping[str, Any] | None,
    asset_base: Path | None,
) -> Mapping[str, Any] | None:
    if assets is None:
        return None
    if isinstance(assets, DossierVisualAssets):
        return assets.to_dict(relative_to=asset_base)
    return dict(assets)
