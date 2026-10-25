"""Application configuration loaded from environment variables and an optional .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunable settings for D3COY. Every weight and threshold is configurable."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- application -----------------------------------------------------
    app_name: str = "D3COY"
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/d3coy.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    seed_on_startup: bool = True

    # --- credentials (never logged or returned) --------------------------
    admin_api_key: SecretStr | None = None
    # Agents must authenticate with a per-agent bearer token (issued via the admin API).
    # Only disable for local experiments with simulated tools; real (sandbox) tools always
    # require an authenticated identity regardless of this flag.
    agent_auth_required: bool = True
    # Shared HMAC key between D3COY and the tool executor for signing permission slips.
    # If unset, a random per-process key is used and the separate executor rejects everything.
    permission_signing_key: SecretStr | None = None
    permission_ttl_seconds: int = Field(default=60, ge=5, le=3600)
    review_ttl_minutes: int = Field(default=60, ge=1)

    # --- real tool executor (sandbox service) ------------------------------------
    executor_url: str = "http://127.0.0.1:8100"
    executor_timeout_seconds: float = 10.0

    # --- live agent demo (server-triggered procurement agent) --------------------
    # When enabled, admins can launch the real LLM agent from the API/dashboard. The agent runs
    # in this process (where the LLM key lives) and drives the normal /api/events pipeline over
    # loopback. Disable in environments where no LLM provider key is configured.
    agent_demo_enabled: bool = True
    # Base URL the in-process agent uses to reach THIS gateway (loopback by default).
    self_base_url: str = "http://127.0.0.1:8000"

    # --- behavioral detection --------------------------------------------
    artifacts_dir: str = "./artifacts"
    auto_train_baseline: bool = True
    behavior_window_size: int = Field(default=10, ge=3, le=100)
    behavior_min_history: int = Field(default=3, ge=1, le=50)
    behavior_long_history: int = Field(default=200, ge=10, le=5000)
    behavior_monitor_score: float = Field(default=50.0, ge=0, le=100)
    behavior_review_score: float = Field(default=65.0, ge=0, le=100)
    river_enabled: bool = True
    river_weight: float = Field(default=0.25, ge=0, le=1)
    river_min_trusted_batch: int = Field(default=25, ge=1)

    # --- semantic intent validation ----------------------------------------
    semantic_enabled: bool = True
    semantic_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    semantic_allow_download: bool = True
    # "none" (default) or "lexical". The lexical fallback is NOT semantic analysis
    # and is always reported under its own detector name.
    semantic_fallback: str = "none"
    semantic_aligned_similarity: float = 0.45
    semantic_misaligned_similarity: float = 0.10

    # --- risk scoring --------------------------------------------------------
    risk_weight_policy: float = 0.40
    risk_weight_behavior: float = 0.25
    risk_weight_semantic: float = 0.20
    risk_weight_context: float = 0.15
    risk_monitor_threshold: float = 25
    risk_review_threshold: float = 50
    risk_block_threshold: float = 75
    fail_closed_on_unavailable_detectors: bool = True

    # --- escalation ----------------------------------------------------------
    escalation_enabled: bool = True
    escalation_block_count: int = Field(default=3, ge=1)
    escalation_window_minutes: int = Field(default=30, ge=1)

    # --- event validation ------------------------------------------------------
    internal_email_domains: str = "d3coy.local"
    max_clock_skew_seconds: int = 300
    max_event_age_hours: int = 24

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        total = (
            self.risk_weight_policy
            + self.risk_weight_behavior
            + self.risk_weight_semantic
            + self.risk_weight_context
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Risk weights must sum to 1.0 (got {total:.3f})")
        if not (0 <= self.risk_monitor_threshold < self.risk_review_threshold < self.risk_block_threshold <= 100):
            raise ValueError("Risk thresholds must satisfy 0 <= monitor < review < block <= 100")
        if self.semantic_fallback not in {"none", "lexical"}:
            raise ValueError("SEMANTIC_FALLBACK must be 'none' or 'lexical'")
        if self.behavior_min_history >= self.behavior_window_size:
            raise ValueError("BEHAVIOR_MIN_HISTORY must be smaller than BEHAVIOR_WINDOW_SIZE")
        return self

    # --- derived helpers -------------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def internal_domain_list(self) -> list[str]:
        return [d.strip().lower() for d in self.internal_email_domains.split(",") if d.strip()]

    @property
    def artifacts_path(self) -> Path:
        return Path(self.artifacts_dir)

    @property
    def admin_key_configured(self) -> bool:
        return self.admin_api_key is not None and bool(self.admin_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
