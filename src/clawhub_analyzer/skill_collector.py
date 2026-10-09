"""Skill detail collection."""

from __future__ import annotations

import logging

from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.db import Database
from clawhub_analyzer.fetcher import Fetcher
from clawhub_analyzer.models import Publisher, Skill, SkillParseBundle, SkillVersion
from clawhub_analyzer.parser import parse_skill_comments, parse_skill_detail, parse_skill_detail_from_api
from clawhub_analyzer.utils import ensure_absolute_url, merge_optional, merge_string_lists

LOGGER = logging.getLogger(__name__)


class SkillCollector:
    def __init__(self, config: AppConfig, db: Database, fetcher: Fetcher):
        self.config = config
        self.db = db
        self.fetcher = fetcher

    async def collect(
        self,
        url: str,
        *,
        is_highlighted: bool | None = None,
        is_popular: bool | None = None,
    ) -> SkillParseBundle:
        html = await self.fetcher.fetch_page(url, wait_for_selector=".skill-hero, h1.section-title")
        raw_html_path = self.fetcher.save_snapshot(url, html, label="skills")
        html_bundle = parse_skill_detail(html, url, self.config.base_url)
        comments = parse_skill_comments(html, html_bundle.skill.skill_id)

        api_bundle: SkillParseBundle | None = None
        slug = html_bundle.skill.skill_id.split("/")[-1]
        api_url = f"{self.config.public_api_base}/api/v1/skills/{slug}"
        try:
            api_payload = await self.fetcher.fetch_json(api_url)
            api_bundle = parse_skill_detail_from_api(api_payload, url, self.config.base_url)
        except Exception as exc:
            LOGGER.debug("Public skill API unavailable for %s: %s", url, exc)

        merged_skill = self._merge_skill_sources(
            api_bundle.skill if api_bundle else None,
            html_bundle.skill,
            raw_html_path=raw_html_path,
            is_highlighted=is_highlighted,
            is_popular=is_popular,
        )
        versions = self._merge_versions(
            api_bundle.versions if api_bundle else [],
            html_bundle.versions,
            skill_id=merged_skill.skill_id,
            slug=slug,
        )
        publisher = self._merge_publisher_sources(api_bundle.publisher if api_bundle else None, html_bundle.publisher)
        permission_hint = html_bundle.permission_hint
        if permission_hint:
            permission_hint.skill_id = merged_skill.skill_id

        bundle = SkillParseBundle(
            skill=merged_skill,
            versions=versions,
            permission_hint=permission_hint,
            publisher=publisher,
            raw_text=html_bundle.raw_text,
            metadata={
                "detail_api_used": api_bundle is not None,
                "raw_html_path": raw_html_path,
                "comment_count": len(comments),
            },
        )

        if not self.config.dry_run:
            self.db.upsert_skill(bundle.skill)
            for version in bundle.versions:
                self.db.upsert_skill_version(version)
            if bundle.publisher:
                self.db.upsert_publisher(bundle.publisher)
            if bundle.permission_hint:
                self.db.upsert_permission_hint(bundle.permission_hint)
            if comments:
                self.db.insert_skill_comments(comments)
        LOGGER.info("Collected skill %s", bundle.skill.skill_id)
        return bundle

    def _merge_skill_sources(
        self,
        api_skill: Skill | None,
        html_skill: Skill,
        *,
        raw_html_path: str | None,
        is_highlighted: bool | None,
        is_popular: bool | None,
    ) -> Skill:
        if not api_skill:
            merged = html_skill
        else:
            merged = Skill(
                skill_id=api_skill.skill_id or html_skill.skill_id,
                name=merge_optional(html_skill.name, api_skill.name),
                page_url=html_skill.page_url,
                description=merge_optional(html_skill.description, api_skill.description),
                author_handle=merge_optional(html_skill.author_handle, api_skill.author_handle),
                author_profile_url=merge_optional(html_skill.author_profile_url, api_skill.author_profile_url),
                stars=merge_optional(html_skill.stars, api_skill.stars),
                downloads=merge_optional(html_skill.downloads, api_skill.downloads),
                installs_current=merge_optional(html_skill.installs_current, api_skill.installs_current),
                installs_all_time=merge_optional(html_skill.installs_all_time, api_skill.installs_all_time),
                comments_count=merge_optional(api_skill.comments_count, html_skill.comments_count),
                versions_count=merge_optional(api_skill.versions_count, html_skill.versions_count),
                is_highlighted=api_skill.is_highlighted or html_skill.is_highlighted,
                is_popular=api_skill.is_popular or html_skill.is_popular,
                is_official=api_skill.is_official or html_skill.is_official,
                is_deprecated=api_skill.is_deprecated or html_skill.is_deprecated,
                is_suspicious=api_skill.is_suspicious or html_skill.is_suspicious,
                is_malware_blocked=api_skill.is_malware_blocked or html_skill.is_malware_blocked,
                install_command=merge_optional(html_skill.install_command, api_skill.install_command),
                current_version=merge_optional(html_skill.current_version, api_skill.current_version),
                created_at=merge_optional(api_skill.created_at, html_skill.created_at),
                updated_at=merge_optional(api_skill.updated_at, html_skill.updated_at),
                scan_status=merge_optional(html_skill.scan_status, api_skill.scan_status),
                scan_report_url=merge_optional(html_skill.scan_report_url, api_skill.scan_report_url),
                category=merge_optional(html_skill.category, api_skill.category),
                tags=merge_string_lists(api_skill.tags, html_skill.tags),
                metadata_os=merge_string_lists(api_skill.metadata_os, html_skill.metadata_os),
                metadata_systems=merge_string_lists(
                    api_skill.metadata_systems, html_skill.metadata_systems
                ),
            )
        merged.is_highlighted = merged.is_highlighted or bool(is_highlighted)
        merged.is_popular = merged.is_popular or bool(is_popular)
        if not merged.author_profile_url and merged.author_handle:
            merged.author_profile_url = ensure_absolute_url(
                self.config.base_url, f"/u/{merged.author_handle}"
            )
        merged.raw_html_path = raw_html_path
        return merged

    def _merge_versions(
        self,
        api_versions: list[SkillVersion],
        html_versions: list[SkillVersion],
        *,
        skill_id: str,
        slug: str,
    ) -> list[SkillVersion]:
        merged: dict[str, SkillVersion] = {}
        for version in api_versions + html_versions:
            merged[version.version] = version if version.version not in merged else SkillVersion(
                skill_id=skill_id,
                version=version.version,
                release_time=merge_optional(version.release_time, merged[version.version].release_time),
                changelog=merge_optional(version.changelog, merged[version.version].changelog),
                bundle_url=merge_optional(version.bundle_url, merged[version.version].bundle_url),
                bundle_hash=merge_optional(version.bundle_hash, merged[version.version].bundle_hash),
                manifest_hash=merge_optional(version.manifest_hash, merged[version.version].manifest_hash),
                dependency_hash=merge_optional(version.dependency_hash, merged[version.version].dependency_hash),
                scan_status=merge_optional(version.scan_status, merged[version.version].scan_status),
                scan_report_url=merge_optional(version.scan_report_url, merged[version.version].scan_report_url),
                security_has_warnings=merged[version.version].security_has_warnings
                or version.security_has_warnings,
                security_checked_at=merge_optional(
                    version.security_checked_at, merged[version.version].security_checked_at
                ),
                security_model=merge_optional(
                    version.security_model, merged[version.version].security_model
                ),
                files_count=merge_optional(version.files_count, merged[version.version].files_count),
            )
        for item in merged.values():
            item.skill_id = skill_id
            if not item.bundle_url:
                item.bundle_url = f"{self.config.public_api_base}/api/v1/download?slug={slug}&version={item.version}"
        return sorted(merged.values(), key=lambda item: item.version, reverse=True)

    @staticmethod
    def _merge_publisher_sources(api_publisher: Publisher | None, html_publisher: Publisher | None) -> Publisher | None:
        if api_publisher and html_publisher:
            return Publisher(
                author_handle=html_publisher.author_handle or api_publisher.author_handle,
                profile_url=html_publisher.profile_url or api_publisher.profile_url,
                display_name=merge_optional(html_publisher.display_name, api_publisher.display_name),
                bio=merge_optional(html_publisher.bio, api_publisher.bio),
                num_skills=merge_optional(html_publisher.num_skills, api_publisher.num_skills),
                total_stars=merge_optional(html_publisher.total_stars, api_publisher.total_stars),
                total_downloads=merge_optional(html_publisher.total_downloads, api_publisher.total_downloads),
            )
        return api_publisher or html_publisher

    # TODO: future experiment hook for bundle download, static analysis, and sandbox tracing.
    async def collect_bundle_artifacts(self, skill: Skill) -> dict:
        return {}
