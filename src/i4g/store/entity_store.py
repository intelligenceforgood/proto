"""Indicator/entity helper queries backed by the ingestion SQL tables."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Literal, Sequence

import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from i4g.store import sql as sql_schema
from i4g.store.sql import session_factory as default_session_factory

LOGGER = logging.getLogger(__name__)

MatchMode = Literal["exact", "prefix", "contains"]


class EntityStore:
    """Query helper that exposes indicator-centric lookups."""

    def __init__(self, session_factory: sessionmaker | None = None) -> None:
        self._session_factory = session_factory or default_session_factory()

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    def search_cases_by_indicator(
        self,
        *,
        indicator_type: str,
        value: str,
        match_mode: MatchMode = "exact",
        datasets: Sequence[str] | None = None,
        loss_buckets: Sequence[str] | None = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Return case matches for a given indicator filter.

        Args:
            indicator_type: Category of indicator (bank_account, crypto_wallet, etc.).
            value: The indicator number/value to search for.
            match_mode: Whether to require exact, prefix, or substring matches.
            datasets: Optional dataset allow-list for scoping results.
            loss_buckets: Optional loss-bucket labels to enforce against case metadata.
            limit: Maximum number of case matches to return.

        Returns:
            A list of dictionaries describing the matched cases/indicators.
        """

        normalized_value = (value or "").strip()
        normalized_type = (indicator_type or "").strip().lower()
        if not normalized_value or not normalized_type:
            return []

        fetch_limit = max(limit * 3, limit + 5, 25)
        dataset_filters = _normalize_list(datasets)
        bucket_ranges = _parse_loss_buckets(loss_buckets)

        with self._session_scope() as session:
            stmt = (
                sa.select(
                    sql_schema.entities.c.case_id,
                    sql_schema.entities.c.entity_type,
                    sql_schema.entities.c.canonical_value,
                    sql_schema.entities.c.metadata.label("entity_metadata"),
                    sql_schema.entities.c.last_seen_at,
                    sql_schema.cases.c.dataset,
                    sql_schema.cases.c.classification,
                    sql_schema.cases.c.metadata.label("case_metadata"),
                )
                .join(sql_schema.cases, sql_schema.cases.c.case_id == sql_schema.entities.c.case_id)
                .where(sa.func.lower(sql_schema.entities.c.entity_type) == normalized_type)
            )

            value_predicate = _value_predicate(sql_schema.entities.c.canonical_value, normalized_value, match_mode)
            stmt = stmt.where(value_predicate)

            if dataset_filters:
                stmt = stmt.where(sa.func.lower(sql_schema.cases.c.dataset).in_(dataset_filters))

            stmt = stmt.order_by(sql_schema.entities.c.last_seen_at.desc().nullslast())
            stmt = stmt.limit(fetch_limit)
            rows = session.execute(stmt).all()

        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            case_id = row.case_id
            if case_id in seen:
                continue

            indicator_metadata = _coerce_metadata(row.entity_metadata)
            case_metadata = _coerce_metadata(row.case_metadata)
            dataset = row.dataset or indicator_metadata.get("dataset") or case_metadata.get("dataset")
            dataset_normalized = (dataset or "").strip().lower()
            if dataset_filters and dataset_normalized not in dataset_filters:
                continue

            loss_amount = _extract_loss_amount(case_metadata, indicator_metadata)
            if bucket_ranges and not _loss_in_buckets(loss_amount, bucket_ranges):
                continue

            results.append(
                {
                    "case_id": case_id,
                    "dataset": dataset,
                    "indicator_type": row.entity_type,
                    "indicator_value": row.canonical_value,
                    "loss_amount": loss_amount,
                    "classification": row.classification,
                }
            )
            seen.add(case_id)
            if len(results) >= limit:
                break

        return results

    def list_datasets(
        self,
        *,
        entity_types: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> List[str]:
        """Return datasets that currently contain the requested entity types."""

        normalized_types = _normalize_list(entity_types)
        with self._session_scope() as session:
            try:
                stmt = (
                    sa.select(
                        sa.func.lower(sql_schema.cases.c.dataset).label("dataset_key"),
                        sql_schema.cases.c.dataset,
                        sa.func.count().label("row_count"),
                    )
                    .join(sql_schema.entities, sql_schema.entities.c.case_id == sql_schema.cases.c.case_id)
                    .where(sql_schema.cases.c.dataset.is_not(None))
                )
                if normalized_types:
                    stmt = stmt.where(sa.func.lower(sql_schema.entities.c.entity_type).in_(normalized_types))
                stmt = stmt.group_by(sql_schema.cases.c.dataset)
                stmt = stmt.order_by(sa.func.count().desc(), sql_schema.cases.c.dataset.asc())
                rows = session.execute(stmt).all()
            except OperationalError as exc:
                if _is_missing_table_error(exc):
                    LOGGER.warning(
                        "Structured store tables are missing; returning an empty dataset list",
                        exc_info=False,
                    )
                    return []
                raise

        datasets: Dict[str, str] = {}
        for row in rows:
            dataset = (row.dataset or "").strip()
            if not dataset:
                continue
            key = row.dataset_key or dataset.lower()
            if key not in datasets:
                datasets[key] = dataset
            if limit is not None and len(datasets) >= limit:
                break
        return list(datasets.values())

    def list_entity_examples(
        self,
        *,
        entity_types: Sequence[str],
        datasets: Sequence[str] | None = None,
        per_type_limit: int = 5,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return representative entity values for each requested type."""

        normalized_types = _normalize_list(entity_types)
        if not normalized_types or per_type_limit <= 0:
            return {}

        dataset_filters = _normalize_list(datasets)
        fetch_limit = max(per_type_limit * 4, per_type_limit)
        results: Dict[str, List[Dict[str, Any]]] = {entity_type: [] for entity_type in normalized_types}

        with self._session_scope() as session:
            try:
                for entity_type in normalized_types:
                    stmt = (
                        sa.select(
                            sql_schema.entities.c.canonical_value,
                            sql_schema.entities.c.metadata.label("entity_metadata"),
                            sql_schema.entities.c.last_seen_at,
                            sql_schema.cases.c.dataset,
                        )
                        .join(sql_schema.cases, sql_schema.cases.c.case_id == sql_schema.entities.c.case_id)
                        .where(sa.func.lower(sql_schema.entities.c.entity_type) == entity_type)
                    )
                    if dataset_filters:
                        stmt = stmt.where(sa.func.lower(sql_schema.cases.c.dataset).in_(dataset_filters))
                    stmt = stmt.order_by(sql_schema.entities.c.last_seen_at.desc().nullslast())
                    stmt = stmt.limit(fetch_limit)
                    rows = session.execute(stmt).all()

                    examples: List[Dict[str, Any]] = []
                    seen_values: set[str] = set()
                    for row in rows:
                        canonical = (row.canonical_value or "").strip()
                        if not canonical:
                            continue
                        key = canonical.lower()
                        if key in seen_values:
                            continue
                        seen_values.add(key)
                        entity_metadata = _coerce_metadata(row.entity_metadata)
                        dataset = row.dataset or entity_metadata.get("dataset")
                        examples.append(
                            {
                                "value": canonical,
                                "dataset": dataset,
                                "last_seen_at": _serialize_timestamp(row.last_seen_at),
                            }
                        )
                        if len(examples) >= per_type_limit:
                            break
                    results[entity_type] = examples
            except OperationalError as exc:
                if _is_missing_table_error(exc):
                    LOGGER.warning(
                        "Structured store tables are missing; returning empty entity example list",
                        exc_info=False,
                    )
                    return {}
                raise

        return results


def _normalize_list(values: Sequence[str] | None) -> List[str]:
    normalized: List[str] = []
    if not values:
        return normalized
    for entry in values:
        if not entry:
            continue
        stripped = entry.strip().lower()
        if stripped:
            normalized.append(stripped)
    return normalized


def _value_predicate(column: sa.ColumnElement[Any], value: str, match_mode: MatchMode) -> sa.ColumnElement[bool]:
    lowered_column = sa.func.lower(column)
    lowered_value = value.lower()
    if match_mode == "exact":
        return lowered_column == lowered_value
    if match_mode == "prefix":
        return lowered_column.like(f"{lowered_value}%")
    if match_mode == "contains":
        return lowered_column.like(f"%{lowered_value}%")
    raise ValueError(f"Unsupported match mode '{match_mode}'")


def _coerce_metadata(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            LOGGER.debug("Failed to parse metadata JSON payload")
    return {}


def _extract_loss_amount(*payloads: Dict[str, Any]) -> float | None:
    for payload in payloads:
        if not payload:
            continue
        for key in ("loss_amount", "loss", "loss_usd"):
            candidate = payload.get(key)
            coerced = _coerce_number(candidate)
            if coerced is not None:
                return coerced
    return None


def _coerce_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _parse_loss_buckets(values: Sequence[str] | None) -> List[tuple[float | None, float | None]]:
    ranges: List[tuple[float | None, float | None]] = []
    if not values:
        return ranges
    for bucket in values:
        if not bucket:
            continue
        parsed = _parse_bucket(bucket)
        if parsed is not None:
            ranges.append(parsed)
    return ranges


def _parse_bucket(label: str) -> tuple[float | None, float | None] | None:
    token = label.strip().lower()
    if not token:
        return None
    if token.startswith("<"):
        upper = _parse_amount(token[1:])
        return (None, upper)
    if token.startswith(">"):
        lower = _parse_amount(token[1:])
        return (lower, None)
    if "-" in token:
        start, end = token.split("-", 1)
        lower = _parse_amount(start)
        upper = _parse_amount(end)
        return (lower, upper)
    return None


def _parse_amount(raw: str) -> float | None:
    token = raw.strip()
    multiplier = 1.0
    if token.endswith("k"):
        multiplier = 1_000.0
        token = token[:-1]
    elif token.endswith("m"):
        multiplier = 1_000_000.0
        token = token[:-1]
    if not token:
        return None
    try:
        return float(token) * multiplier
    except ValueError:
        return None


def _loss_in_buckets(value: float | None, buckets: Sequence[tuple[float | None, float | None]]) -> bool:
    if value is None:
        return False
    for lower, upper in buckets:
        if lower is not None and value < lower:
            continue
        if upper is not None and value > upper:
            continue
        return True
    return False


def _serialize_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        target = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return target.astimezone(timezone.utc).isoformat()
    if isinstance(value, str):
        return value
    return str(value)


def _is_missing_table_error(error: OperationalError) -> bool:
    """Return True when the SQL backend reports a missing-table error."""

    message = str(error).lower()
    if "no such table" in message:
        return True
    if "does not exist" in message:
        return True
    return False


__all__ = ["EntityStore", "MatchMode"]
