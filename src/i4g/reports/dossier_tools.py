"""LangChain tool suite for dossier generation workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from i4g.reports.dossier_analysis import DossierAnalysis
from i4g.reports.dossier_context import DossierContextResult
from i4g.reports.dossier_visuals import DossierVisualAssets


class DossierToolInput(BaseModel):
    """Serialized context supplied to every LangChain tool."""

    plan: Mapping[str, Any]
    context: Mapping[str, Any] | None = None
    analysis: Mapping[str, Any]
    assets: Mapping[str, Any] | None = None


def _normalise_cases(plan: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    cases = plan.get("cases") or []
    if not isinstance(cases, list):
        return []
    return [case for case in cases if isinstance(case, Mapping)]


def _context_map(context: Mapping[str, Any] | None) -> Dict[str, Mapping[str, Any]]:
    if not context:
        return {}
    result: Dict[str, Mapping[str, Any]] = {}
    for case in context.get("cases", []):
        if isinstance(case, Mapping):
            case_id = str(case.get("case_id") or "")
            if case_id:
                result[case_id] = case
    return result


class GeoReasonerTool(BaseTool):
    """Summarises jurisdiction mix and cross-border patterns."""

    name: str = "geo_reasoner"
    description: str = "Analyze bundled cases for geographic signals and cross-border hints."
    args_schema: type[DossierToolInput] = DossierToolInput

    def _run(self, plan: DossierToolInput) -> str:
        cases = _normalise_cases(plan.plan)
        jurisdiction_counts: MutableMapping[str, int] = {}
        cross_border_cases: List[str] = []
        for case in cases:
            jurisdiction = str(case.get("jurisdiction") or "unknown").upper()
            jurisdiction_counts[jurisdiction] = jurisdiction_counts.get(jurisdiction, 0) + 1
            if case.get("cross_border"):
                cross_border_cases.append(str(case.get("case_id")))
        ordered_regions = sorted(jurisdiction_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        payload = {
            "jurisdiction_counts": jurisdiction_counts,
            "primary_regions": [region for region, _ in ordered_regions],
            "cross_border_cases": cross_border_cases,
            "warnings": [],
        }
        return json.dumps(payload)

    async def _arun(self, *args, **kwargs):  # pragma: no cover - async not used
        raise NotImplementedError


class TimelineSynthesizerTool(BaseTool):
    """Builds a concise timeline table for dossier narratives."""

    name: str = "timeline_synthesizer"
    description: str = "Generate timestamped events for every bundled case."
    args_schema: type[DossierToolInput] = DossierToolInput

    def _run(self, plan: DossierToolInput) -> str:
        cases = _normalise_cases(plan.plan)
        context_lookup = _context_map(plan.context)
        events: List[Dict[str, Any]] = []
        for case in cases:
            timestamp = str(case.get("accepted_at") or plan.plan.get("created_at"))
            summary = self._case_summary(case, context_lookup.get(str(case.get("case_id")) or ""))
            events.append(
                {
                    "timestamp": timestamp,
                    "case_id": case.get("case_id"),
                    "summary": summary,
                    "loss_amount_usd": case.get("loss_amount_usd"),
                }
            )
        events.sort(key=lambda item: item["timestamp"] or "")
        payload = {
            "events": events[:30],
            "warnings": [] if events else ["No accepted cases were available for the timeline"],
        }
        return json.dumps(payload)

    def _case_summary(
        self,
        case: Mapping[str, Any],
        context: Mapping[str, Any] | None,
    ) -> str:
        if context and context.get("review"):
            review = context["review"]
            note = review.get("summary") or review.get("notes")
            if note:
                return str(note)
        entities = case.get("primary_entities") or []
        entity_preview = ", ".join(entities[:2]) if isinstance(entities, list) else ""
        if entity_preview:
            return f"Linked indicators: {entity_preview}"
        return f"Loss recorded: ${case.get('loss_amount_usd')}"

    async def _arun(self, *args, **kwargs):  # pragma: no cover - async not used
        raise NotImplementedError


class EntityGraphTool(BaseTool):
    """Derives an entity-to-case adjacency list for bundle visualization."""

    name: str = "entity_graph"
    description: str = "Highlight overlapping entities across all cases."
    args_schema: type[DossierToolInput] = DossierToolInput

    def _run(self, plan: DossierToolInput) -> str:
        cases = _normalise_cases(plan.plan)
        adjacency: MutableMapping[str, List[str]] = {}
        for case in cases:
            case_id = str(case.get("case_id") or "")
            for entity in case.get("primary_entities", []) or []:
                normalized = str(entity).strip()
                if not normalized:
                    continue
                adjacency.setdefault(normalized, []).append(case_id)
        clusters = sorted(
            ((entity, len(set(case_ids))) for entity, case_ids in adjacency.items()),
            key=lambda item: (-item[1], item[0]),
        )[:5]
        payload = {
            "entities": {entity: sorted(set(case_ids)) for entity, case_ids in adjacency.items()},
            "entity_count": len(adjacency),
            "top_clusters": [{"entity": entity, "count": count} for entity, count in clusters],
        }
        return json.dumps(payload)

    async def _arun(self, *args, **kwargs):  # pragma: no cover - async not used
        raise NotImplementedError


class ChartRendererTool(BaseTool):
    """Summarises rendered visual assets for downstream templates."""

    name: str = "chart_renderer"
    description: str = "Expose generated chart and geo assets to the agent."
    args_schema: type[DossierToolInput] = DossierToolInput

    def _run(self, plan: DossierToolInput) -> str:
        assets = plan.assets or {}
        payload = {
            "timeline_chart": assets.get("timeline_chart"),
            "geojson_path": assets.get("geojson"),
            "geo_map_image": assets.get("geo_map_image"),
            "warnings": assets.get("warnings", []),
        }
        return json.dumps(payload)

    async def _arun(self, *args, **kwargs):  # pragma: no cover - async not used
        raise NotImplementedError


class NarrativeWriterTool(BaseTool):
    """Produces a deterministic narrative summary for the dossier."""

    name: str = "narrative_report"
    description: str = "Draft a concise narrative using plan and analysis metadata."
    args_schema: type[DossierToolInput] = DossierToolInput

    def _run(self, plan: DossierToolInput) -> str:
        summary = self._build_summary(plan)
        payload = {
            "summary": summary,
            "risk_level": "elevated" if plan.plan.get("cross_border") else "standard",
            "recommendation": self._recommendation(plan),
            "confidence": 0.85,
        }
        return json.dumps(payload)

    def _build_summary(self, tool_input: DossierToolInput) -> str:
        total_loss = Decimal(tool_input.plan.get("total_loss_usd", "0") or "0")
        loss_display = f"${total_loss:,.0f}"
        jurisdiction = tool_input.plan.get("jurisdiction_key") or "mixed"
        case_count = tool_input.analysis.get("case_count")
        range_payload = tool_input.analysis.get("accepted_range") or {}
        earliest = range_payload.get("earliest") or "unknown"
        latest = range_payload.get("latest") or "unknown"
        return (
            f"{case_count} high-loss cases tied to {jurisdiction} generated {loss_display} in reported loss "
            f"between {earliest} and {latest}."
        )

    def _recommendation(self, tool_input: DossierToolInput) -> str:
        if tool_input.plan.get("cross_border"):
            return "Escalate to cross-border task force with immediate signature verification."
        return "Share dossier with regional investigators and request subpoena follow-up."

    async def _arun(self, *args, **kwargs):  # pragma: no cover - async not used
        raise NotImplementedError


@dataclass(frozen=True)
class DossierToolResults:
    """Container describing the complete tool execution outputs."""

    outputs: Mapping[str, Any]
    warnings: Sequence[str]
    errors: Mapping[str, str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outputs": dict(self.outputs),
            "warnings": list(self.warnings),
            "errors": dict(self.errors),
        }


class DossierToolSuite:
    """Orchestrates every LangChain tool used during dossier generation."""

    def __init__(self, tools: Sequence[BaseTool] | None = None) -> None:
        self._tools = (
            list(tools)
            if tools
            else [
                GeoReasonerTool(),
                TimelineSynthesizerTool(),
                EntityGraphTool(),
                ChartRendererTool(),
                NarrativeWriterTool(),
            ]
        )

    def run(
        self,
        *,
        plan: DossierPlan,
        context: DossierContextResult | None,
        analysis: DossierAnalysis,
        assets: DossierVisualAssets | None,
        asset_base: Path | None = None,
    ) -> DossierToolResults:
        """Execute every tool and aggregate the structured outputs."""

        asset_payload = assets.to_dict(relative_to=asset_base) if assets else None
        input_payload = DossierToolInput(
            plan=plan.to_dict(),
            context=context.to_dict() if context else None,
            analysis=analysis.to_dict(),
            assets=asset_payload,
        )
        outputs: Dict[str, Any] = {}
        warnings: List[str] = []
        errors: Dict[str, str] = {}
        for tool in self._tools:
            try:
                raw = tool._run(input_payload)
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                outputs[tool.name] = parsed
            except Exception as exc:  # pragma: no cover - defensive guardrail
                errors[tool.name] = str(exc)
                warnings.append(f"{tool.name} failed: {exc}")
        return DossierToolResults(outputs=outputs, warnings=tuple(warnings), errors=errors)


__all__ = [
    "DossierToolInput",
    "DossierToolResults",
    "DossierToolSuite",
    "EntityGraphTool",
    "GeoReasonerTool",
    "NarrativeWriterTool",
    "TimelineSynthesizerTool",
    "ChartRendererTool",
]
