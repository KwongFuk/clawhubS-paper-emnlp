"""Configuration management."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from clawhub_analyzer.sample_queries import get_default_queries


class AppConfig(BaseSettings):
    """Runtime configuration with .env support."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CLAWHUB_",
        case_sensitive=False,
        extra="ignore",
    )

    base_url: str = "https://clawhub.ai"
    public_api_base: str = "https://clawhub.ai"
    public_api_skills_path: str = "/api/v1/skills"
    skills_path: str = "/skills"
    user_agent: str = "ClawHubAnalyzer/0.1 (+academic research crawler; public pages only)"
    requests_per_second: float = 0.5
    concurrent_requests: int = 4
    use_playwright: bool = True
    output_dir: Path = Path("data")
    snapshot_dir: Path = Path("data/html_snapshots")
    db_path: Path = Path("data/experiment1_split/merged.sqlite3")
    failure_log_path: Path = Path("data/failures.jsonl")
    save_html: bool = True
    max_pages: int = 100
    request_timeout: float = 20.0
    retries: int = 3
    robots_aware: bool = True
    dry_run: bool = False
    search_queries: list[str] = Field(default_factory=get_default_queries)
    search_top_k: int = 20
    log_level: str = "INFO"
    playwright_timeout_ms: int = 15_000
    browse_scroll_steps: int = 8
    search_scroll_steps: int = 4
    api_page_size: int = 200
    api_index_sort: str = "downloads"
    api_index_max_pages: int = 10_000
    render_routes: list[str] = Field(default_factory=lambda: ["/", "/skills", "/u/"])

    @field_validator("search_queries", mode="before")
    @classmethod
    def _parse_queries(cls, value: Any) -> list[str]:
        if value is None:
            return get_default_queries()
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise TypeError("search_queries must be a comma-separated string or list")

    @field_validator("output_dir", "snapshot_dir", "db_path", "failure_log_path", mode="before")
    @classmethod
    def _coerce_path(cls, value: Any) -> Path:
        if isinstance(value, Path):
            return value
        return Path(str(value))

    def ensure_directories(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.failure_log_path.parent.mkdir(parents=True, exist_ok=True)

    def with_overrides(self, **overrides: Any) -> "AppConfig":
        cleaned = {key: value for key, value in overrides.items() if value is not None}
        config = AppConfig(**{**self.model_dump(), **cleaned})
        config.ensure_directories()
        return config


def load_config(**overrides: Any) -> AppConfig:
    config = AppConfig()
    config = config.with_overrides(**overrides)
    return config
