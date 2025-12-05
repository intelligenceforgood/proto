"""Configuration loader for i4g services using Pydantic settings."""

from __future__ import annotations

import json
import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

ENV_VAR_NAME = "I4G_ENV"
DEFAULT_ENV = "local"


def _env_project_root(var_name: str) -> Path | None:
    """Resolve an override path from the provided environment variable."""

    raw_value = os.getenv(var_name)
    if not raw_value:
        return None
    candidate = Path(raw_value).expanduser().resolve()
    return candidate


def _detect_project_root() -> Path:
    """Return the repository root, honoring environment overrides when set."""

    for env_var in ("I4G_PROJECT_ROOT", "I4G_RUNTIME__PROJECT_ROOT"):
        candidate = _env_project_root(env_var)
        if candidate:
            return candidate

    resolved = Path(__file__).resolve()
    for parent in resolved.parents:
        marker = parent / "pyproject.toml"
        if marker.exists() and (parent / "src").exists():
            return parent
    # Fallback to the historical layout (proto/src/i4g/settings/.. => repo root at parents[3]).
    return resolved.parents[3]


PROJECT_ROOT = _detect_project_root()
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_CONFIG_FILE = CONFIG_DIR / "settings.default.toml"
LOCAL_CONFIG_FILE = CONFIG_DIR / "settings.local.toml"
SETTINGS_FILE_ENV_VAR = "I4G_SETTINGS_FILE"


def _resolve_env(explicit_env: str | None = None) -> str:
    """Return the active environment name.

    Args:
        explicit_env: Environment value supplied directly by the caller.

    Returns:
        A stripped environment name, falling back to ``DEFAULT_ENV``.
    """

    env = explicit_env or os.getenv(ENV_VAR_NAME) or DEFAULT_ENV
    return env.strip()


def _env_file_candidates(env: str) -> list[Path]:
    """List candidate ``.env`` files used during settings resolution.

    Args:
        env: Active environment name (for example, ``local`` or ``staging``).

    Returns:
        Ordered list of paths that should be considered when loading
        environment variables from disk.
    """

    return [
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / f".env.{env}",
        PROJECT_ROOT / ".env.local",
    ]


def _resolve_config_path(raw_path: str | None) -> Path | None:
    """Return an absolute config path from user input."""

    if not raw_path:
        return None
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()
    return candidate


def _config_file_priority(include_missing: bool = False) -> tuple[Path, ...]:
    """Return config files in descending precedence order."""

    ordered: list[Path] = []
    env_override = _resolve_config_path(os.getenv(SETTINGS_FILE_ENV_VAR))
    if env_override:
        ordered.append(env_override)
    ordered.append(LOCAL_CONFIG_FILE)
    ordered.append(DEFAULT_CONFIG_FILE)
    if include_missing:
        return tuple(ordered)
    existing: list[Path] = []
    for path in ordered:
        if path.exists():
            existing.append(path)
    return tuple(existing)


class TomlConfigSettingsSource(PydanticBaseSettingsSource):
    """Pydantic settings source that loads values from a TOML file."""

    def __init__(self, settings_cls: type[BaseSettings], path: Path) -> None:
        super().__init__(settings_cls)
        self.path = path
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.path.exists():
            self._data = {}
            return self._data
        try:
            with self.path.open("rb") as handle:
                self._data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:  # pragma: no cover - invalid files surface immediately
            raise ValueError(f"Invalid TOML syntax in {self.path}") from exc
        return self._data

    def __call__(self) -> dict[str, Any]:  # pragma: no cover - trivial wrapper
        return self._load()

    def get_field_value(self, field_name: str, field):  # pragma: no cover - passthrough helper
        data = self._load()
        return data.get(field_name), field_name in data


def _read_env_value(*keys: str) -> str | None:
    """Return the first present environment variable from ``keys``."""

    for key in keys:
        value = os.getenv(key)
        if value is not None:
            return value
    return None


class RuntimeSettings(BaseSettings):
    """Process-level runtime controls."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("LOG_LEVEL", "RUNTIME__LOG_LEVEL"),
    )


class APISettings(BaseSettings):
    """API endpoint configuration shared by CLI + dashboards."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    base_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias=AliasChoices("API_URL", "API__BASE_URL"),
    )
    key: str = Field(
        default="dev-analyst-token",
        validation_alias=AliasChoices("API_KEY", "API__KEY"),
    )


class IdentitySettings(BaseSettings):
    """Identity provider wiring for auth-enabled services."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    provider: Literal["mock", "google_identity", "authentik", "firebase"] = Field(
        default="mock",
        validation_alias=AliasChoices("IDENTITY_PROVIDER", "IDENTITY__PROVIDER"),
    )
    audience: str | None = Field(
        default=None,
        validation_alias=AliasChoices("IDENTITY_AUDIENCE", "IDENTITY__AUDIENCE"),
    )
    issuer: str | None = Field(
        default=None,
        validation_alias=AliasChoices("IDENTITY_ISSUER", "IDENTITY__ISSUER"),
    )
    client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("IDENTITY_CLIENT_ID", "IDENTITY__CLIENT_ID"),
    )
    disable_auth: bool = Field(
        default=False,
        validation_alias=AliasChoices("IDENTITY_DISABLE_AUTH", "IDENTITY__DISABLE_AUTH"),
    )


class StorageSettings(BaseSettings):
    """Structured + blob storage configuration."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    structured_backend: Literal["sqlite", "firestore", "cloudsql"] = Field(
        default="sqlite",
        validation_alias=AliasChoices("STRUCTURED_BACKEND", "STORAGE__STRUCTURED_BACKEND"),
    )
    sqlite_path: Path = Field(
        default=PROJECT_ROOT / "data" / "i4g_store.db",
        # validation_alias=AliasChoices("SQLITE_PATH", "STORAGE__SQLITE_PATH"),
    )
    firestore_project: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FIRESTORE_PROJECT", "STORAGE__FIRESTORE__PROJECT"),
    )
    firestore_collection: str = Field(
        default="cases",
        validation_alias=AliasChoices("FIRESTORE_COLLECTION", "STORAGE__FIRESTORE__COLLECTION"),
    )
    evidence_bucket: str | None = Field(
        default=None,
        validation_alias=AliasChoices("STORAGE_EVIDENCE_BUCKET", "STORAGE__EVIDENCE_BUCKET"),
    )
    evidence_local_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "evidence",
        validation_alias=AliasChoices("STORAGE_EVIDENCE_LOCAL_DIR", "STORAGE__EVIDENCE__LOCAL_DIR"),
    )
    reports_bucket: str | None = Field(
        default=None,
        # validation_alias=AliasChoices("STORAGE_REPORTS_BUCKET", "STORAGE__REPORTS_BUCKET"),
    )
    cloudsql_instance: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CLOUDSQL_INSTANCE", "STORAGE__CLOUDSQL__INSTANCE"),
    )
    cloudsql_database: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CLOUDSQL_DATABASE", "STORAGE__CLOUDSQL__DATABASE"),
    )


class VectorSettings(BaseSettings):
    """Vector store configuration supporting multiple backends."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    backend: Literal["chroma", "faiss", "pgvector", "vertex_ai"] = Field(
        default="chroma",
        validation_alias=AliasChoices("VECTOR_BACKEND", "VECTOR__BACKEND"),
    )
    collection: str = Field(
        default="i4g_vectors",
        validation_alias=AliasChoices("VECTOR_COLLECTION", "VECTOR__COLLECTION"),
    )
    embedding_model: str = Field(
        default="nomic-embed-text",
        validation_alias=AliasChoices("EMBED_MODEL", "VECTOR__EMBED_MODEL"),
    )
    chroma_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "chroma_store",
        # validation_alias=AliasChoices("VECTOR_CHROMA_DIR", "VECTOR__CHROMA_DIR"),
    )
    faiss_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "faiss_store",
        validation_alias=AliasChoices("VECTOR_FAISS_DIR", "VECTOR__FAISS_DIR"),
    )
    pgvector_dsn: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VECTOR_PGVECTOR_DSN", "VECTOR__PGVECTOR__DSN"),
    )
    vertex_ai_index: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VECTOR_VERTEX_AI_INDEX", "VECTOR__VERTEX_AI__INDEX"),
    )
    vertex_ai_project: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VECTOR_VERTEX_AI_PROJECT",
            "VECTOR__VERTEX_AI__PROJECT",
            "I4G_VERTEX_SEARCH_PROJECT",
        ),
    )
    vertex_ai_location: str | None = Field(
        default="us-central1",
        validation_alias=AliasChoices(
            "VECTOR_VERTEX_AI_LOCATION",
            "VECTOR__VERTEX_AI__LOCATION",
            "I4G_VERTEX_SEARCH_LOCATION",
        ),
    )
    vertex_ai_data_store: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VECTOR_VERTEX_AI_DATA_STORE",
            "VECTOR__VERTEX_AI__DATA_STORE",
            "I4G_VERTEX_SEARCH_DATA_STORE",
        ),
    )
    vertex_ai_branch: str = Field(
        default="default_branch",
        validation_alias=AliasChoices(
            "VECTOR_VERTEX_AI_BRANCH",
            "VECTOR__VERTEX_AI__BRANCH",
            "I4G_VERTEX_SEARCH_BRANCH",
        ),
    )


class LLMSettings(BaseSettings):
    """Large language model provider settings."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    provider: Literal["ollama", "vertex_ai", "mock"] = Field(
        default="ollama",
        validation_alias=AliasChoices("LLM_PROVIDER", "LLM__PROVIDER"),
    )
    chat_model: str = Field(
        default="llama3",
        validation_alias=AliasChoices("LLM_CHAT_MODEL", "LLM__CHAT_MODEL"),
    )
    temperature: float = Field(
        default=0.1,
        validation_alias=AliasChoices("LLM_TEMPERATURE", "LLM__TEMPERATURE"),
    )
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        validation_alias=AliasChoices("OLLAMA_BASE_URL", "LLM__OLLAMA_BASE_URL"),
    )
    vertex_ai_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_VERTEX_AI_MODEL", "LLM__VERTEX_AI__MODEL"),
    )
    vertex_ai_project: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_VERTEX_AI_PROJECT", "LLM__VERTEX_AI__PROJECT"),
    )
    vertex_ai_location: str | None = Field(
        default="us-central1",
        validation_alias=AliasChoices("LLM_VERTEX_AI_LOCATION", "LLM__VERTEX_AI__LOCATION"),
    )


class SecretsSettings(BaseSettings):
    """Secret resolution strategy (local vs Secret Manager)."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    use_secret_manager: bool = Field(
        default=False,
        validation_alias=AliasChoices("SECRETS_USE_SECRET_MANAGER", "SECRETS__USE_SECRET_MANAGER"),
    )
    project: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SECRETS_PROJECT", "SECRETS__PROJECT"),
    )
    local_env_file: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("SECRETS_LOCAL_ENV_FILE", "SECRETS__LOCAL_ENV_FILE"),
    )


class IngestionSettings(BaseSettings):
    """Scheduler + job configuration for ingestion workflows."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    enable_scheduled_jobs: bool = Field(
        default=False,
        validation_alias=AliasChoices("INGESTION_ENABLE_SCHEDULED_JOBS", "INGESTION__ENABLE_SCHEDULED_JOBS"),
    )
    default_region: str = Field(
        default="us-central1",
        validation_alias=AliasChoices("INGESTION_DEFAULT_REGION", "INGESTION__DEFAULT_REGION"),
    )
    scheduler_project: str | None = Field(
        default=None,
        validation_alias=AliasChoices("INGESTION_SCHEDULER_PROJECT", "INGESTION__SCHEDULER_PROJECT"),
    )
    default_service_account: str | None = Field(
        default=None,
        validation_alias=AliasChoices("INGESTION_SERVICE_ACCOUNT", "INGESTION__SERVICE_ACCOUNT"),
    )
    enable_sql: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "INGEST_ENABLE_SQL",
            "INGEST__ENABLE_SQL",
            "INGESTION_ENABLE_SQL",
            "INGESTION__ENABLE_SQL",
        ),
    )
    enable_firestore: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "INGEST_ENABLE_FIRESTORE",
            "INGEST__ENABLE_FIRESTORE",
            "INGESTION_ENABLE_FIRESTORE",
            "INGESTION__ENABLE_FIRESTORE",
        ),
    )
    enable_vertex: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "INGEST_ENABLE_VERTEX",
            "INGEST__ENABLE_VERTEX",
            "INGESTION_ENABLE_VERTEX",
            "INGESTION__ENABLE_VERTEX",
        ),
    )
    enable_vector_store: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "INGEST_ENABLE_VECTOR",
            "INGEST__ENABLE_VECTOR",
            "INGESTION_ENABLE_VECTOR",
            "INGESTION__ENABLE_VECTOR",
        ),
    )
    dataset_path: Path = Field(
        default=PROJECT_ROOT / "data" / "retrieval_poc" / "cases.jsonl",
        validation_alias=AliasChoices(
            "INGEST_JSONL_PATH",
            "INGEST__JSONL_PATH",
            "INGESTION_JSONL_PATH",
            "INGESTION__JSONL_PATH",
        ),
    )
    batch_limit: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "INGEST_BATCH_LIMIT",
            "INGEST__BATCH_LIMIT",
            "INGESTION_BATCH_LIMIT",
            "INGESTION__BATCH_LIMIT",
        ),
    )
    dry_run: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "INGEST_DRY_RUN",
            "INGEST__DRY_RUN",
            "INGESTION_DRY_RUN",
            "INGESTION__DRY_RUN",
        ),
    )
    reset_vector: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "INGEST_RESET_VECTOR",
            "INGEST__RESET_VECTOR",
            "INGESTION_RESET_VECTOR",
            "INGESTION__RESET_VECTOR",
        ),
    )
    default_dataset: str = Field(
        default="unknown",
        validation_alias=AliasChoices(
            "INGEST_DEFAULT_DATASET",
            "INGEST__DEFAULT_DATASET",
            "INGESTION_DEFAULT_DATASET",
            "INGESTION__DEFAULT_DATASET",
        ),
    )
    fanout_timeout_seconds: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "INGEST_FANOUT_TIMEOUT_SECONDS",
            "INGEST__FANOUT_TIMEOUT_SECONDS",
            "INGESTION_FANOUT_TIMEOUT_SECONDS",
            "INGESTION__FANOUT_TIMEOUT_SECONDS",
        ),
    )
    max_retries: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "INGEST_MAX_RETRIES",
            "INGEST__MAX_RETRIES",
            "INGESTION_MAX_RETRIES",
            "INGESTION__MAX_RETRIES",
        ),
    )
    retry_delay_seconds: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "INGEST_RETRY_DELAY_SECONDS",
            "INGEST__RETRY_DELAY_SECONDS",
            "INGESTION_RETRY_DELAY_SECONDS",
            "INGESTION__RETRY_DELAY_SECONDS",
        ),
    )


class ObservabilitySettings(BaseSettings):
    """Logging, tracing, and metrics configuration."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    structured_logging: bool = Field(
        default=True,
        validation_alias=AliasChoices("OBS_STRUCTURED_LOGGING", "OBSERVABILITY__STRUCTURED_LOGGING"),
    )
    otlp_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OBS_OTLP_ENDPOINT", "OBSERVABILITY__OTLP_ENDPOINT"),
    )
    trace_sample_rate: float = Field(
        default=0.0,
        validation_alias=AliasChoices("OBS_TRACE_SAMPLE_RATE", "OBSERVABILITY__TRACE_SAMPLE_RATE"),
    )
    statsd_host: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OBS_STATSD_HOST", "OBSERVABILITY__STATSD_HOST"),
    )
    statsd_port: int = Field(
        default=8125,
        validation_alias=AliasChoices("OBS_STATSD_PORT", "OBSERVABILITY__STATSD_PORT"),
    )
    statsd_prefix: str = Field(
        default="i4g",
        validation_alias=AliasChoices("OBS_STATSD_PREFIX", "OBSERVABILITY__STATSD_PREFIX"),
    )
    service_name: str = Field(
        default="i4g-backend",
        validation_alias=AliasChoices("OBS_SERVICE_NAME", "OBSERVABILITY__SERVICE_NAME"),
    )


class AccountListSettings(BaseSettings):
    """Account list extraction configuration."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("ACCOUNT_LIST_ENABLED", "ACCOUNT_LIST__ENABLED"),
    )
    require_api_key: bool = Field(
        default=True,
        validation_alias=AliasChoices("ACCOUNT_LIST_REQUIRE_API_KEY", "ACCOUNT_LIST__REQUIRE_API_KEY"),
    )
    api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ACCOUNT_LIST_API_KEY", "ACCOUNT_LIST__API_KEY"),
    )
    header_name: str = Field(
        default="X-ACCOUNTLIST-KEY",
        validation_alias=AliasChoices("ACCOUNT_LIST_HEADER_NAME", "ACCOUNT_LIST__HEADER_NAME"),
    )
    max_top_k: int = Field(
        default=250,
        validation_alias=AliasChoices("ACCOUNT_LIST_MAX_TOP_K", "ACCOUNT_LIST__MAX_TOP_K"),
    )
    default_formats: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("ACCOUNT_LIST_DEFAULT_FORMATS", "ACCOUNT_LIST__DEFAULT_FORMATS"),
    )
    artifact_prefix: str = Field(
        default="account_list",
        validation_alias=AliasChoices("ACCOUNT_LIST_ARTIFACT_PREFIX", "ACCOUNT_LIST__ARTIFACT_PREFIX"),
    )
    drive_folder_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ACCOUNT_LIST_DRIVE_FOLDER_ID", "ACCOUNT_LIST__DRIVE_FOLDER_ID"),
    )
    enable_vector: bool = Field(
        default=True,
        validation_alias=AliasChoices("ACCOUNT_LIST_ENABLE_VECTOR", "ACCOUNT_LIST__ENABLE_VECTOR"),
    )


class SavedSearchSettings(BaseSettings):
    """Saved-search migration defaults shared across CLI scripts."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    migration_tag: str = Field(
        default="hybrid-v1",
        validation_alias=AliasChoices(
            "SEARCH_SAVED_SEARCH_MIGRATION_TAG",
            "SEARCH__SAVED_SEARCH__MIGRATION_TAG",
            "SAVED_SEARCH_MIGRATION_TAG",
        ),
    )
    schema_version: str = Field(
        default="",
        validation_alias=AliasChoices(
            "SEARCH_SAVED_SEARCH_SCHEMA_VERSION",
            "SEARCH__SAVED_SEARCH__SCHEMA_VERSION",
            "SAVED_SEARCH_SCHEMA_VERSION",
        ),
    )


class SearchSettings(BaseSettings):
    """Hybrid search tuning parameters and schema presets."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    semantic_weight: float = Field(
        default=0.65,
        validation_alias=AliasChoices("SEARCH_SEMANTIC_WEIGHT", "SEARCH__SEMANTIC_WEIGHT"),
    )
    structured_weight: float = Field(
        default=0.35,
        validation_alias=AliasChoices("SEARCH_STRUCTURED_WEIGHT", "SEARCH__STRUCTURED_WEIGHT"),
    )
    default_limit: int = Field(
        default=25,
        validation_alias=AliasChoices("SEARCH_DEFAULT_LIMIT", "SEARCH__DEFAULT_LIMIT"),
    )
    schema_cache_ttl_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices("SEARCH_SCHEMA_CACHE_TTL", "SEARCH__SCHEMA_CACHE_TTL"),
    )
    indicator_types: list[str] = Field(
        default_factory=lambda: [
            "bank_account",
            "crypto_wallet",
            "email",
            "phone",
            "ip_address",
            "asn",
            "browser_agent",
            "url",
            "merchant",
        ],
        validation_alias=AliasChoices("SEARCH_INDICATOR_TYPES", "SEARCH__INDICATOR_TYPES"),
    )
    dataset_presets: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("SEARCH_DATASET_PRESETS", "SEARCH__DATASET_PRESETS"),
    )
    classification_presets: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("SEARCH_CLASSIFICATION_PRESETS", "SEARCH__CLASSIFICATION_PRESETS"),
    )
    time_presets: list[str] = Field(
        default_factory=lambda: ["7d", "30d", "90d"],
        validation_alias=AliasChoices("SEARCH_TIME_PRESETS", "SEARCH__TIME_PRESETS"),
    )
    loss_buckets: list[str] = Field(
        default_factory=lambda: ["<10k", "10k-50k", ">50k"],
        validation_alias=AliasChoices("SEARCH_LOSS_BUCKETS", "SEARCH__LOSS_BUCKETS"),
    )
    schema_entity_example_limit: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "SEARCH_SCHEMA_ENTITY_EXAMPLE_LIMIT",
            "SEARCH__SCHEMA_ENTITY_EXAMPLE_LIMIT",
        ),
    )
    saved_search: SavedSearchSettings = Field(default_factory=SavedSearchSettings)

    @model_validator(mode="after")
    def _validate_weights(self) -> "SearchSettings":
        """Ensure semantic/structured weights fall within acceptable bounds."""

        for field_name in ("semantic_weight", "structured_weight"):
            value = getattr(self, field_name)
            if value < 0 or value > 1:
                raise ValueError(f"{field_name} must be between 0 and 1 inclusive (got {value})")
        if self.semantic_weight == 0 and self.structured_weight == 0:
            raise ValueError("At least one of semantic_weight or structured_weight must be greater than zero.")
        return self


class ReportSettings(BaseSettings):
    """Agentic dossier/report configuration."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    drive_parent_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("REPORT_DRIVE_PARENT_ID", "REPORT__DRIVE_PARENT_ID"),
    )
    min_loss_usd: float = Field(
        default=50000.0,
        validation_alias=AliasChoices("REPORT_MIN_LOSS_USD", "REPORT__MIN_LOSS_USD"),
    )
    recency_days: int = Field(
        default=30,
        validation_alias=AliasChoices("REPORT_RECENCY_DAYS", "REPORT__RECENCY_DAYS"),
    )
    max_cases_per_dossier: int = Field(
        default=5,
        validation_alias=AliasChoices("REPORT_MAX_CASES_PER_DOSSIER", "REPORT__MAX_CASES_PER_DOSSIER"),
    )
    require_cross_border: bool = Field(
        default=False,
        validation_alias=AliasChoices("REPORT_REQUIRE_CROSS_BORDER", "REPORT__REQUIRE_CROSS_BORDER"),
    )
    hash_algorithm: str = Field(
        default="sha256",
        validation_alias=AliasChoices("REPORT_HASH_ALGORITHM", "REPORT__HASH_ALGORITHM"),
    )


class Settings(BaseSettings):
    """Top-level configuration model with nested sections for each subsystem."""

    env: str = Field(
        default_factory=lambda: _resolve_env(),
        validation_alias=AliasChoices("ENV", "ENVIRONMENT", "RUNTIME__ENV"),
    )
    project_root: Path = Field(
        default=PROJECT_ROOT,
        validation_alias=AliasChoices("PROJECT_ROOT", "RUNTIME__PROJECT_ROOT", "I4G_RUNTIME__PROJECT_ROOT"),
    )
    data_dir: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "data",
        # validation_alias=AliasChoices("DATA_DIR", "RUNTIME__DATA_DIR"),
    )
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    api: APISettings = Field(default_factory=APISettings)
    identity: IdentitySettings = Field(default_factory=IdentitySettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    vector: VectorSettings = Field(default_factory=VectorSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    secrets: SecretsSettings = Field(default_factory=SecretsSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    account_list: AccountListSettings = Field(default_factory=AccountListSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    report: ReportSettings = Field(default_factory=ReportSettings)
    env_files: tuple[Path, ...] = Field(default_factory=tuple, exclude=True)
    config_files: tuple[Path, ...] = Field(default_factory=tuple, exclude=True)

    model_config = SettingsConfigDict(
        env_prefix="I4G_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """Extend settings sources with TOML-based config files."""

        config_sources = [TomlConfigSettingsSource(settings_cls, path) for path in _config_file_priority()]
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            *config_sources,
            file_secret_settings,
        )

    @model_validator(mode="after")
    def _resolve_paths(self) -> "Settings":
        """Normalize relative paths once the model is initialised."""

        if not self.data_dir.is_absolute():
            object.__setattr__(self, "data_dir", (self.project_root / self.data_dir).resolve())

        storage_updates = {}
        if not self.storage.sqlite_path.is_absolute():
            storage_updates["sqlite_path"] = (self.project_root / self.storage.sqlite_path).resolve()
        if not self.storage.evidence_local_dir.is_absolute():
            storage_updates["evidence_local_dir"] = (self.project_root / self.storage.evidence_local_dir).resolve()
        if storage_updates:
            object.__setattr__(self, "storage", self.storage.model_copy(update=storage_updates))

        vector_updates = {}
        if not self.vector.chroma_dir.is_absolute():
            vector_updates["chroma_dir"] = (self.project_root / self.vector.chroma_dir).resolve()
        if not self.vector.faiss_dir.is_absolute():
            vector_updates["faiss_dir"] = (self.project_root / self.vector.faiss_dir).resolve()
        if vector_updates:
            object.__setattr__(self, "vector", self.vector.model_copy(update=vector_updates))

        if self.secrets.local_env_file and not self.secrets.local_env_file.is_absolute():
            secrets_update = {"local_env_file": (self.project_root / self.secrets.local_env_file).resolve()}
            object.__setattr__(self, "secrets", self.secrets.model_copy(update=secrets_update))

        self._normalize_ingestion_paths()

        return self

    def _normalize_ingestion_paths(self) -> None:
        """Ensure ingestion paths resolve relative to the project root."""

        dataset_path = self.ingestion.dataset_path
        normalized = dataset_path
        if dataset_path and not isinstance(dataset_path, Path):
            normalized = Path(dataset_path)
        if normalized and not normalized.is_absolute():
            normalized = (self.project_root / normalized).resolve()
        if normalized and normalized != dataset_path:
            object.__setattr__(self, "ingestion", self.ingestion.model_copy(update={"dataset_path": normalized}))

    @model_validator(mode="after")
    def _apply_environment_overrides(self) -> "Settings":
        """Force environment-specific defaults after basic resolution."""

        env_name = self.env.lower()

        if env_name == "local":
            identity_update = {
                "provider": "mock",
                "disable_auth": True,
                "audience": None,
                "issuer": None,
                "client_id": None,
            }
            object.__setattr__(self, "identity", self.identity.model_copy(update=identity_update))

            storage_update = {
                "structured_backend": "sqlite",
                "firestore_project": None,
                "cloudsql_instance": None,
                "cloudsql_database": None,
                "evidence_bucket": None,
                "reports_bucket": None,
            }
            object.__setattr__(self, "storage", self.storage.model_copy(update=storage_update))

            vector_update = {
                "backend": "chroma",
                "collection": self.vector.collection or "i4g_vectors",
                "pgvector_dsn": None,
                "vertex_ai_index": None,
                "vertex_ai_project": None,
            }
            object.__setattr__(self, "vector", self.vector.model_copy(update=vector_update))

            llm_update = {
                "provider": "ollama",
                "vertex_ai_model": None,
                "vertex_ai_project": None,
            }
            object.__setattr__(self, "llm", self.llm.model_copy(update=llm_update))

            secrets_update = {"use_secret_manager": False, "project": None}
            if not self.secrets.local_env_file:
                secrets_update["local_env_file"] = self.project_root / ".env.local"
            object.__setattr__(self, "secrets", self.secrets.model_copy(update=secrets_update))

            ingestion_update = {
                "enable_scheduled_jobs": False,
                "scheduler_project": None,
                "default_service_account": None,
            }
            object.__setattr__(self, "ingestion", self.ingestion.model_copy(update=ingestion_update))

            observability_update = {"structured_logging": False, "otlp_endpoint": None}
            object.__setattr__(self, "observability", self.observability.model_copy(update=observability_update))

        ingestion_alias_updates: dict[str, object] = {}

        def _legacy_env_keys(*keys: str) -> tuple[str, ...]:
            resolved: list[str] = []
            seen: set[str] = set()
            for key in keys:
                for candidate in (f"I4G_{key}", key):
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    resolved.append(candidate)
            return tuple(resolved)

        def _ingestion_bool(field: str, *keys: str) -> None:
            value = _read_env_value(*_legacy_env_keys(*keys))
            if value is None:
                return
            lowered = value.strip().lower()
            ingestion_alias_updates[field] = lowered not in {"false", "0", "off", "no"}

        def _ingestion_str(field: str, *keys: str) -> None:
            value = _read_env_value(*_legacy_env_keys(*keys))
            if value is None:
                return
            ingestion_alias_updates[field] = value.strip()

        def _ingestion_int(field: str, *keys: str) -> None:
            value = _read_env_value(*_legacy_env_keys(*keys))
            if value is None:
                return
            try:
                ingestion_alias_updates[field] = int(value.strip())
            except ValueError:
                pass

        _ingestion_bool(
            "enable_scheduled_jobs",
            "INGESTION__ENABLE_SCHEDULED_JOBS",
            "INGESTION_ENABLE_SCHEDULED_JOBS",
            "INGEST__ENABLE_SCHEDULED_JOBS",
            "INGEST_ENABLE_SCHEDULED_JOBS",
        )
        _ingestion_bool(
            "enable_sql",
            "INGESTION__ENABLE_SQL",
            "INGESTION_ENABLE_SQL",
            "INGEST__ENABLE_SQL",
            "INGEST_ENABLE_SQL",
        )
        _ingestion_bool(
            "enable_firestore",
            "INGESTION__ENABLE_FIRESTORE",
            "INGESTION_ENABLE_FIRESTORE",
            "INGEST__ENABLE_FIRESTORE",
            "INGEST_ENABLE_FIRESTORE",
        )
        _ingestion_bool(
            "enable_vertex",
            "INGESTION__ENABLE_VERTEX",
            "INGESTION_ENABLE_VERTEX",
            "INGEST__ENABLE_VERTEX",
            "INGEST_ENABLE_VERTEX",
        )
        _ingestion_bool(
            "enable_vector_store",
            "INGESTION__ENABLE_VECTOR_STORE",
            "INGESTION_ENABLE_VECTOR_STORE",
            "INGESTION__ENABLE_VECTOR",
            "INGESTION_ENABLE_VECTOR",
            "INGEST__ENABLE_VECTOR_STORE",
            "INGEST_ENABLE_VECTOR_STORE",
            "INGEST__ENABLE_VECTOR",
            "INGEST_ENABLE_VECTOR",
        )
        _ingestion_str(
            "default_region",
            "INGESTION__DEFAULT_REGION",
            "INGESTION_DEFAULT_REGION",
            "INGEST__DEFAULT_REGION",
            "INGEST_DEFAULT_REGION",
        )
        _ingestion_str(
            "scheduler_project",
            "INGESTION__SCHEDULER_PROJECT",
            "INGESTION_SCHEDULER_PROJECT",
            "INGEST__SCHEDULER_PROJECT",
            "INGEST_SCHEDULER_PROJECT",
        )
        _ingestion_str(
            "default_service_account",
            "INGESTION__SERVICE_ACCOUNT",
            "INGESTION_SERVICE_ACCOUNT",
            "INGEST__SERVICE_ACCOUNT",
            "INGEST_SERVICE_ACCOUNT",
        )
        _ingestion_str(
            "default_dataset",
            "INGESTION__DEFAULT_DATASET",
            "INGESTION_DEFAULT_DATASET",
            "INGEST__DEFAULT_DATASET",
            "INGEST_DEFAULT_DATASET",
        )
        _ingestion_str(
            "dataset_path",
            "INGESTION__JSONL_PATH",
            "INGESTION_JSONL_PATH",
            "INGEST__JSONL_PATH",
            "INGEST_JSONL_PATH",
        )
        _ingestion_int(
            "fanout_timeout_seconds",
            "INGESTION__FANOUT_TIMEOUT_SECONDS",
            "INGESTION_FANOUT_TIMEOUT_SECONDS",
            "INGEST__FANOUT_TIMEOUT_SECONDS",
            "INGEST_FANOUT_TIMEOUT_SECONDS",
        )
        _ingestion_int(
            "batch_limit",
            "INGESTION__BATCH_LIMIT",
            "INGESTION_BATCH_LIMIT",
            "INGEST__BATCH_LIMIT",
            "INGEST_BATCH_LIMIT",
        )
        _ingestion_int(
            "max_retries",
            "INGESTION__MAX_RETRIES",
            "INGESTION_MAX_RETRIES",
            "INGEST__MAX_RETRIES",
            "INGEST_MAX_RETRIES",
        )
        _ingestion_int(
            "retry_delay_seconds",
            "INGESTION__RETRY_DELAY_SECONDS",
            "INGESTION_RETRY_DELAY_SECONDS",
            "INGEST__RETRY_DELAY_SECONDS",
            "INGEST_RETRY_DELAY_SECONDS",
        )
        _ingestion_bool(
            "dry_run",
            "INGESTION__DRY_RUN",
            "INGESTION_DRY_RUN",
            "INGEST__DRY_RUN",
            "INGEST_DRY_RUN",
        )
        _ingestion_bool(
            "reset_vector",
            "INGESTION__RESET_VECTOR",
            "INGESTION_RESET_VECTOR",
            "INGEST__RESET_VECTOR",
            "INGEST_RESET_VECTOR",
        )

        if ingestion_alias_updates:
            object.__setattr__(self, "ingestion", self.ingestion.model_copy(update=ingestion_alias_updates))
            self._normalize_ingestion_paths()

        provider_override = _read_env_value(
            "I4G_LLM__PROVIDER",
            "I4G_LLM_PROVIDER",
            "LLM__PROVIDER",
            "LLM_PROVIDER",
        )
        if provider_override:
            llm_updates = {"provider": provider_override.strip().lower()}
            object.__setattr__(self, "llm", self.llm.model_copy(update=llm_updates))

        account_list_updates: dict[str, object] = {}
        header_override = _read_env_value(
            "I4G_ACCOUNT_LIST__HEADER_NAME",
            "I4G_ACCOUNT_LIST_HEADER_NAME",
            "ACCOUNT_LIST__HEADER_NAME",
            "ACCOUNT_LIST_HEADER_NAME",
        )
        if header_override:
            account_list_updates["header_name"] = header_override.strip()

        require_override = _read_env_value(
            "I4G_ACCOUNT_LIST__REQUIRE_API_KEY",
            "I4G_ACCOUNT_LIST_REQUIRE_API_KEY",
            "ACCOUNT_LIST__REQUIRE_API_KEY",
            "ACCOUNT_LIST_REQUIRE_API_KEY",
        )
        if require_override is not None:
            lowered = require_override.strip().lower()
            account_list_updates["require_api_key"] = lowered not in {"false", "0", "off", "no"}

        formats_override = _read_env_value(
            "I4G_ACCOUNT_LIST__DEFAULT_FORMATS",
            "I4G_ACCOUNT_LIST_DEFAULT_FORMATS",
            "ACCOUNT_LIST__DEFAULT_FORMATS",
            "ACCOUNT_LIST_DEFAULT_FORMATS",
        )
        if formats_override:
            parsed_formats: list[str] = []
            try:
                candidate = json.loads(formats_override)
                if isinstance(candidate, list):
                    parsed_formats = [str(item).strip() for item in candidate if str(item).strip()]
            except json.JSONDecodeError:
                pass
            if not parsed_formats:
                parsed_formats = [chunk.strip() for chunk in formats_override.split(",") if chunk.strip()]
            account_list_updates["default_formats"] = parsed_formats

        if account_list_updates:
            object.__setattr__(self, "account_list", self.account_list.model_copy(update=account_list_updates))

        return self

    @property
    def log_level(self) -> str:
        """str: Effective logging level for the running process."""

        return self.runtime.log_level

    @property
    def api_base_url(self) -> str:
        """str: Base URL for API calls (used by scripts and dashboards)."""

        return self.api.base_url

    @property
    def api_key(self) -> str:
        """str: Shared API token for simple authenticated endpoints."""

        return self.api.key

    @property
    def sqlite_path(self) -> Path:
        """Path: Filesystem path for the local SQLite database."""

        return self.storage.sqlite_path

    @property
    def vector_backend(self) -> str:
        """str: Name of the configured vector store backend."""

        return self.vector.backend

    @property
    def vector_collection(self) -> str:
        """str: Default collection or index identifier for vector storage."""

        return self.vector.collection

    @property
    def embedding_model(self) -> str:
        """str: Embedding model identifier used for vector generation."""

        return self.vector.embedding_model

    @property
    def chroma_dir(self) -> Path:
        """Path: Directory where Chroma persists its state."""

        return self.vector.chroma_dir

    @property
    def faiss_dir(self) -> Path:
        """Path: Directory where FAISS index artifacts are stored."""

        return self.vector.faiss_dir

    @property
    def ollama_base_url(self) -> str:
        """str: Base URL for the Ollama HTTP API."""

        return self.llm.ollama_base_url

    @property
    def is_local(self) -> bool:
        """bool: True when the active environment is ``local``."""

        return self.env.lower() == "local"


def _load_settings(env: str | None = None) -> Settings:
    """Load settings with optional environment override.

    Args:
        env: Environment name supplied programmatically.

    Returns:
        Fully parsed :class:`Settings` instance with env files applied.
    """

    resolved_env = _resolve_env(env)
    candidate_files = [path for path in _env_file_candidates(resolved_env) if path.exists()]
    config_files = _config_file_priority()
    return Settings(
        _env_file=[str(path) for path in candidate_files],
        _env_file_encoding="utf-8",
        env=resolved_env,
        env_files=tuple(candidate_files),
        config_files=config_files,
    )


@lru_cache(maxsize=1)
def get_settings(env: str | None = None) -> Settings:
    """Return cached settings for the requested environment."""

    return _load_settings(env)


def reload_settings(env: str | None = None) -> Settings:
    """Clear the cached settings and reload from disk."""

    get_settings.cache_clear()
    return get_settings(env)


__all__ = [
    "Settings",
    "get_settings",
    "reload_settings",
    "PROJECT_ROOT",
    "ENV_VAR_NAME",
]
