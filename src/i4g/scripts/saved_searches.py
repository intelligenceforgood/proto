"""Helpers for saved-search export and tagging workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from i4g.settings import get_settings

SETTINGS = get_settings()


def build_argument_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser for the tagging helper."""

    parser = argparse.ArgumentParser(description="Annotate saved-search JSON exports with migration metadata.")
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the JSON file produced by i4g-admin export-saved-searches.",
    )
    parser.add_argument(
        "--output",
        help="Destination file. Defaults to overwriting the --input file if omitted.",
    )
    parser.add_argument(
        "--tag",
        default=SETTINGS.search.saved_search.migration_tag,
        help=(
            "Tag to append to each saved search" f" (default: {SETTINGS.search.saved_search.migration_tag or 'none'})."
        ),
    )
    parser.add_argument(
        "--schema-version",
        default=SETTINGS.search.saved_search.schema_version,
        help=(
            "Optional schema version to inject into each record's params"
            f" (default: {SETTINGS.search.saved_search.schema_version or 'blank'})."
        ),
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help="Remove duplicate tags (case-insensitive) after annotation.",
    )
    return parser


def load_records(path: Path) -> list[dict[str, Any]]:
    """Load saved-search JSON payloads from disk."""

    content = path.read_text(encoding="utf-8")
    payload = json.loads(content)
    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]
    if isinstance(payload, dict):
        return [payload]
    raise ValueError("Input file must contain a JSON object or array of objects.")


def _normalize_tags(values: Any) -> list[str]:
    if isinstance(values, list):
        return [str(tag) for tag in values if isinstance(tag, (str, int, float)) and str(tag).strip()]
    if isinstance(values, str) and values.strip():
        return [values.strip()]
    return []


def annotate_records(
    records: list[dict[str, Any]], *, tag: str, schema_version: str, dedupe: bool
) -> list[dict[str, Any]]:
    """Return annotated saved-search records."""

    normalized_tag = tag.strip()
    normalized_schema = schema_version.strip()
    for record in records:
        tags = _normalize_tags(record.get("tags"))
        if normalized_tag:
            tags.append(normalized_tag)
            if dedupe:
                seen = set()
                unique_tags: list[str] = []
                for value in tags:
                    lowered = value.lower()
                    if lowered in seen:
                        continue
                    seen.add(lowered)
                    unique_tags.append(value)
                tags = unique_tags
        record["tags"] = tags

        if normalized_schema:
            params = record.get("params")
            if not isinstance(params, dict):
                params = {}
            params["schema_version"] = normalized_schema
            record["params"] = params
    return records


def annotate_file(
    input_path: Path,
    *,
    output_path: Path | None = None,
    tag: str,
    schema_version: str,
    dedupe: bool,
) -> tuple[Path, int]:
    """Annotate a saved-search export file in place and return the output path and record count."""

    records = load_records(input_path)
    annotated = annotate_records(records, tag=tag, schema_version=schema_version, dedupe=dedupe)
    destination = output_path or input_path
    destination.write_text(json.dumps(annotated, indent=2) + "\n", encoding="utf-8")
    return destination, len(annotated)


def main() -> None:
    """CLI entrypoint for the tagging helper."""

    args = build_argument_parser().parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else None
    destination, count = annotate_file(
        input_path,
        output_path=output_path,
        tag=args.tag,
        schema_version=args.schema_version,
        dedupe=args.dedupe,
    )
    print(f"Annotated {count} saved search(es); wrote {destination}")


__all__ = [
    "annotate_records",
    "annotate_file",
    "build_argument_parser",
    "load_records",
    "main",
]
