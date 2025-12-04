"""Lightweight dossier generation scaffolding used by the queue processor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Sequence

from i4g.reports.bundle_builder import DossierPlan
from i4g.reports.dossier_agent_payload import build_agent_payload
from i4g.reports.dossier_analysis import analyze_plan
from i4g.reports.dossier_context import DossierContextLoader, DossierContextResult
from i4g.reports.dossier_signatures import generate_signature_manifest
from i4g.reports.dossier_visuals import DossierVisualAssets, DossierVisualBuilder
from i4g.services.factories import build_dossier_context_loader
from i4g.settings import get_settings


@dataclass(frozen=True)
class DossierGenerationResult:
    """Container describing generated dossier artifacts."""

    plan_id: str
    artifacts: Sequence[Path]
    warnings: Sequence[str]


class DossierGenerator:
    """Prototype dossier generator that emits JSON manifests for downstream tooling."""

    def __init__(
        self,
        *,
        artifact_dir: Path | None = None,
        context_loader: DossierContextLoader | None = None,
        visuals_builder: DossierVisualBuilder | None = None,
    ) -> None:
        settings = get_settings()
        base_dir = artifact_dir or (settings.data_dir / "reports" / "dossiers")
        base_dir.mkdir(parents=True, exist_ok=True)
        self._artifact_dir = base_dir
        self._context_loader = context_loader or build_dossier_context_loader()
        self._visuals_builder = visuals_builder or DossierVisualBuilder(base_dir=base_dir)
        self._hash_algorithm = settings.report.hash_algorithm

    def generate_from_plan(self, plan: DossierPlan) -> DossierGenerationResult:
        """Persist a serialized dossier plan and return the artifact location."""

        payload = plan.to_dict()
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        payload["case_count"] = len(plan.cases)
        analysis = analyze_plan(plan)
        payload["analysis"] = analysis.to_dict()
        warnings: List[str] = []
        assets: DossierVisualAssets | None = None
        context: DossierContextResult | None = None

        if self._context_loader:
            context = self._context_loader.load(plan)
            payload["context"] = context.to_dict()
            warnings.extend(context.warnings)
        else:
            payload["context"] = None

        if self._visuals_builder:
            assets = self._visuals_builder.render(plan)
            payload["assets"] = assets.to_dict()
            warnings.extend(assets.warnings)
        else:
            payload["assets"] = None

        destination = self._artifact_dir / f"{plan.plan_id}.json"
        signature_path = destination.with_suffix(".signatures.json")
        payload["signature_manifest"] = {
            "path": str(signature_path),
            "algorithm": self._hash_algorithm,
        }

        payload["agent_payload"] = build_agent_payload(plan=plan, context=context, analysis=analysis).to_dict()

        destination.write_text(json.dumps(payload, indent=2))

        signature_entries = [("manifest", destination)]
        if assets:
            signature_entries.extend(
                [
                    ("timeline_chart", assets.timeline_chart),
                    ("geo_map_image", assets.geo_map_image),
                    ("geojson", assets.geojson_path),
                ]
            )
        signature_manifest = generate_signature_manifest(signature_entries, algorithm=self._hash_algorithm)
        signature_path.write_text(json.dumps(signature_manifest.to_dict(), indent=2))
        warnings.extend(signature_manifest.warnings)

        artifacts = [destination, signature_path]
        return DossierGenerationResult(plan_id=plan.plan_id, artifacts=artifacts, warnings=warnings)
