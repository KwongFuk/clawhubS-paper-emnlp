"""Pydantic data models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from clawhub_analyzer.utils import now_utc_iso


class ModelBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class Skill(ModelBase):
    skill_id: str
    name: str | None = None
    page_url: str
    description: str | None = None
    author_handle: str | None = None
    author_profile_url: str | None = None
    stars: int | None = None
    downloads: int | None = None
    installs_current: int | None = None
    installs_all_time: int | None = None
    comments_count: int | None = None
    versions_count: int | None = None
    is_highlighted: bool = False
    is_popular: bool = False
    is_official: bool = False
    is_deprecated: bool = False
    is_suspicious: bool = False
    is_malware_blocked: bool = False
    install_command: str | None = None
    current_version: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    scan_status: str | None = None
    scan_report_url: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata_os: list[str] = Field(default_factory=list)
    metadata_systems: list[str] = Field(default_factory=list)
    raw_html_path: str | None = None
    collected_at: str = Field(default_factory=now_utc_iso)


class SkillVersion(ModelBase):
    skill_id: str
    version: str
    release_time: str | None = None
    changelog: str | None = None
    bundle_url: str | None = None
    bundle_hash: str | None = None
    manifest_hash: str | None = None
    dependency_hash: str | None = None
    scan_status: str | None = None
    scan_report_url: str | None = None
    security_has_warnings: bool = False
    security_checked_at: str | None = None
    security_model: str | None = None
    files_count: int | None = None
    collected_at: str = Field(default_factory=now_utc_iso)


class SkillFile(ModelBase):
    skill_id: str
    version: str
    path: str
    size: int | None = None
    sha256: str | None = None
    content_type: str | None = None
    text_path: str | None = None
    collected_at: str = Field(default_factory=now_utc_iso)


class SkillComment(ModelBase):
    skill_id: str
    comment_index: int
    author_handle: str | None = None
    body: str | None = None
    collected_at: str = Field(default_factory=now_utc_iso)


class SkillArchiveStatus(ModelBase):
    slug: str
    skill_id: str | None = None
    detail_completed: bool = False
    versions_completed: bool = False
    files_completed: bool = False
    file_texts_completed: bool = False
    last_error: str | None = None
    last_attempt_at: str = Field(default_factory=now_utc_iso)
    completed_at: str | None = None


class Publisher(ModelBase):
    author_handle: str
    profile_url: str
    display_name: str | None = None
    bio: str | None = None
    num_skills: int | None = None
    total_stars: int | None = None
    total_downloads: int | None = None
    collected_at: str = Field(default_factory=now_utc_iso)


class SearchResult(ModelBase):
    query: str
    rank: int
    skill_id: str
    skill_name: str | None = None
    result_url: str
    timestamp: str = Field(default_factory=now_utc_iso)


class PermissionOrSecretHint(ModelBase):
    skill_id: str
    requires_api_key: bool = False
    requires_oauth: bool = False
    requires_token: bool = False
    requires_local_file_access: bool = False
    requires_shell_access: bool = False
    requires_browser_access: bool = False
    requires_github: bool = False
    requires_slack: bool = False
    requires_notion: bool = False
    requires_calendar: bool = False
    requires_email: bool = False
    requires_docs: bool = False
    requires_pdf: bool = False
    raw_evidence: list[str] = Field(default_factory=list)


class SkillCard(ModelBase):
    skill_id: str
    name: str | None = None
    page_url: str
    description: str | None = None
    author_handle: str | None = None
    author_profile_url: str | None = None
    stars: int | None = None
    downloads: int | None = None
    tags: list[str] = Field(default_factory=list)
    is_highlighted: bool = False
    is_popular: bool = False


class HomeParseResult(ModelBase):
    highlighted_skills: list[SkillCard] = Field(default_factory=list)
    popular_skills: list[SkillCard] = Field(default_factory=list)

    @property
    def discovered_skill_urls(self) -> list[str]:
        return [card.page_url for card in self.highlighted_skills + self.popular_skills]


class PublisherParseResult(ModelBase):
    publisher: Publisher
    published_skill_urls: list[str] = Field(default_factory=list)


class SkillParseBundle(ModelBase):
    skill: Skill
    versions: list[SkillVersion] = Field(default_factory=list)
    permission_hint: PermissionOrSecretHint | None = None
    publisher: Publisher | None = None
    raw_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
