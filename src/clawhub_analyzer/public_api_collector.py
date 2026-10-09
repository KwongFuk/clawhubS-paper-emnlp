"""Official public HTTP API collection helpers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.db import Database
from clawhub_analyzer.fetcher import Fetcher
from clawhub_analyzer.models import (
    PermissionOrSecretHint,
    Publisher,
    SkillArchiveStatus,
    Skill,
    SkillFile,
    SkillParseBundle,
    SkillVersion,
)
from clawhub_analyzer.parser import (
    extract_permission_hints,
    parse_skill_detail_from_api,
    parse_version_detail_from_api,
)
from clawhub_analyzer.utils import (
    clean_multiline_text,
    coerce_iso_datetime,
    ensure_absolute_url,
    make_snapshot_path,
    merge_optional,
    merge_string_lists,
    now_utc_iso,
    write_text_file,
)

LOGGER = logging.getLogger(__name__)

_TEXT_FILE_SUFFIXES = {
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".jsonc",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".example",
    ".js",
    ".cjs",
    ".mjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".py",
    ".rb",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".php",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".sql",
    ".graphql",
    ".xml",
    ".html",
    ".css",
    ".csv",
}


@dataclass
class ApiIndexSummary:
    pages_fetched: int = 0
    slugs_discovered: int = 0
    skills_upserted: int = 0
    versions_upserted: int = 0
    publisher_records_upserted: int = 0
    discovered_skill_urls: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    next_cursor: str | None = None


@dataclass
class SkillArchiveResult:
    bundle: SkillParseBundle
    files: list[SkillFile] = field(default_factory=list)
    file_texts_saved: int = 0


@dataclass
class VersionCollectionResult:
    versions: list[SkillVersion] = field(default_factory=list)
    files: list[SkillFile] = field(default_factory=list)
    file_texts: list[str] = field(default_factory=list)
    file_texts_saved: int = 0


@dataclass
class ApiArchiveSummary(ApiIndexSummary):
    skills_processed: int = 0
    skills_skipped: int = 0
    files_upserted: int = 0
    file_texts_saved: int = 0
    permission_hints_upserted: int = 0


@dataclass
class ArchiveResumeState:
    mode: str
    sort: str
    page_size: int
    shard_index: int
    shard_count: int
    next_page_number: int = 1
    next_cursor: str | None = None
    completed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "sort": self.sort,
            "page_size": self.page_size,
            "shard_index": self.shard_index,
            "shard_count": self.shard_count,
            "next_page_number": self.next_page_number,
            "next_cursor": self.next_cursor,
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ArchiveResumeState":
        return cls(
            mode=str(payload.get("mode") or ""),
            sort=str(payload.get("sort") or ""),
            page_size=int(payload.get("page_size") or 0),
            shard_index=int(payload.get("shard_index") or 0),
            shard_count=int(payload.get("shard_count") or 1),
            next_page_number=int(payload.get("next_page_number") or 1),
            next_cursor=payload.get("next_cursor") or None,
            completed=bool(payload.get("completed", False)),
        )


class PublicApiCollector:
    """Collect index and detail metadata from ClawHub's public HTTP API."""

    def __init__(self, config: AppConfig, db: Database, fetcher: Fetcher):
        self.config = config
        self.db = db
        self.fetcher = fetcher

    async def _fetch_index_payload_with_recovery(
        self,
        *,
        sort: str,
        page_size: int,
        cursor: str | None,
        page_number: int,
    ) -> dict[str, Any]:
        delay_seconds = 10.0
        max_attempts = max(8, self.config.retries * 4)
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                return await self.fetcher.fetch_json(
                    self._build_list_url(sort=sort, page_size=page_size, cursor=cursor)
                )
            except Exception as exc:  # pragma: no cover - network path
                last_error = exc
                LOGGER.warning(
                    "Index page fetch failed for page=%s cursor=%s attempt=%s/%s: %s",
                    page_number,
                    "yes" if cursor else "no",
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt >= max_attempts:
                    break
                await asyncio.sleep(delay_seconds)
                delay_seconds = min(delay_seconds * 1.5, 120.0)

        assert last_error is not None
        raise last_error

    async def collect_skill_index(
        self,
        *,
        sort: str | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
        shard_index: int = 0,
        shard_count: int = 1,
        resume_state_path: Path | None = None,
    ) -> ApiIndexSummary:
        summary = ApiIndexSummary()
        seen_urls: set[str] = set()
        pages_limit = max_pages or self.config.api_index_max_pages
        page_size = page_size or self.config.api_page_size
        sort = sort or self.config.api_index_sort
        resume_state = self._load_resume_state(
            resume_state_path,
            mode="index",
            sort=sort,
            page_size=page_size,
            shard_index=shard_index,
            shard_count=shard_count,
        )
        if resume_state and resume_state.completed:
            LOGGER.info(
                "Resume state marks index collection complete at %s; nothing to do.",
                resume_state_path,
            )
            summary.next_cursor = None
            return summary
        start_page = resume_state.next_page_number if resume_state else 1
        cursor = resume_state.next_cursor if resume_state else None

        for page_number in range(start_page, pages_limit + 1):
            payload = await self._fetch_index_payload_with_recovery(
                sort=sort,
                page_size=page_size,
                cursor=cursor,
                page_number=page_number,
            )
            raw_items = payload.get("items") or []
            cursor = payload.get("nextCursor") or None
            items = self._filter_items_for_shard(
                raw_items,
                shard_index=shard_index,
                shard_count=shard_count,
            )
            summary.pages_fetched = page_number
            summary.slugs_discovered += len(items)

            if not raw_items:
                summary.next_cursor = cursor
                self._save_resume_state(
                    resume_state_path,
                    ArchiveResumeState(
                        mode="index",
                        sort=sort,
                        page_size=page_size,
                        shard_index=shard_index,
                        shard_count=shard_count,
                        next_page_number=page_number + 1,
                        next_cursor=cursor,
                        completed=not bool(cursor),
                    ),
                )
                LOGGER.info(
                    "Index page %s: raw_items=0, advancing cursor=%s",
                    page_number,
                    "yes" if cursor else "no",
                )
                if not cursor:
                    break
                continue
            if not items:
                summary.next_cursor = cursor
                self._save_resume_state(
                    resume_state_path,
                    ArchiveResumeState(
                        mode="index",
                        sort=sort,
                        page_size=page_size,
                        shard_index=shard_index,
                        shard_count=shard_count,
                        next_page_number=page_number + 1,
                        next_cursor=cursor,
                        completed=not bool(cursor),
                    ),
                )
                LOGGER.info(
                    "Index page %s: shard_received=0, advancing cursor=%s",
                    page_number,
                    "yes" if cursor else "no",
                )
                if not cursor:
                    break
                continue

            bundles = await self._collect_page_items(items)
            for bundle in bundles:
                if bundle is None:
                    continue
                summary.skills_upserted += 1
                summary.versions_upserted += len(bundle.versions)
                if bundle.publisher:
                    summary.publisher_records_upserted += 1
                if bundle.skill.page_url and bundle.skill.page_url not in seen_urls:
                    seen_urls.add(bundle.skill.page_url)
                    summary.discovered_skill_urls.append(bundle.skill.page_url)

            summary.next_cursor = cursor
            self._save_resume_state(
                resume_state_path,
                ArchiveResumeState(
                    mode="index",
                    sort=sort,
                    page_size=page_size,
                    shard_index=shard_index,
                    shard_count=shard_count,
                    next_page_number=page_number + 1,
                    next_cursor=cursor,
                    completed=not bool(cursor),
                ),
            )
            if not cursor:
                break

        return summary

    async def collect_public_archive(
        self,
        *,
        sort: str | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
        max_skills: int | None = None,
        shard_index: int = 0,
        shard_count: int = 1,
        include_versions: bool = True,
        include_files: bool = True,
        include_file_texts: bool = True,
        max_version_pages: int | None = None,
        version_page_size: int = 100,
        max_files_per_version: int | None = None,
        resume_state_path: Path | None = None,
    ) -> ApiArchiveSummary:
        summary = ApiArchiveSummary()
        seen_urls: set[str] = set()
        pages_limit = max_pages or self.config.api_index_max_pages
        page_size = page_size or self.config.api_page_size
        sort = sort or self.config.api_index_sort
        remaining = max_skills
        resume_state = self._load_resume_state(
            resume_state_path,
            mode="archive",
            sort=sort,
            page_size=page_size,
            shard_index=shard_index,
            shard_count=shard_count,
        )
        if resume_state and resume_state.completed:
            LOGGER.info(
                "Resume state marks archive collection complete at %s; nothing to do.",
                resume_state_path,
            )
            summary.next_cursor = None
            return summary
        start_page = resume_state.next_page_number if resume_state else 1
        cursor = resume_state.next_cursor if resume_state else None

        LOGGER.info(
            "Starting public archive: sort=%s page_size=%s max_pages=%s max_skills=%s shard=%s/%s versions=%s files=%s file_texts=%s resume_page=%s",
            sort,
            page_size,
            pages_limit,
            max_skills or "all",
            shard_index,
            shard_count,
            include_versions,
            include_files,
            include_file_texts,
            start_page,
        )
        if not self.config.dry_run:
            bootstrapped = self.db.bootstrap_skill_archive_statuses()
            LOGGER.info("Bootstrapped existing archive statuses from DB: %s", bootstrapped)

        for page_number in range(start_page, pages_limit + 1):
            payload = await self._fetch_index_payload_with_recovery(
                sort=sort,
                page_size=page_size,
                cursor=cursor,
                page_number=page_number,
            )
            raw_items = payload.get("items") or []
            cursor = payload.get("nextCursor") or None
            items = self._filter_items_for_shard(
                raw_items,
                shard_index=shard_index,
                shard_count=shard_count,
            )
            summary.pages_fetched = page_number
            summary.slugs_discovered += len(items)

            if not raw_items:
                summary.next_cursor = cursor
                self._save_resume_state(
                    resume_state_path,
                    ArchiveResumeState(
                        mode="archive",
                        sort=sort,
                        page_size=page_size,
                        shard_index=shard_index,
                        shard_count=shard_count,
                        next_page_number=page_number + 1,
                        next_cursor=cursor,
                        completed=not bool(cursor),
                    ),
                )
                LOGGER.info(
                    "Archive page %s: raw_items=0, advancing cursor=%s",
                    page_number,
                    "yes" if cursor else "no",
                )
                if not cursor:
                    break
                continue
            if remaining is not None:
                if remaining <= 0:
                    break
                items = items[:remaining]
            if not items:
                summary.next_cursor = cursor
                self._save_resume_state(
                    resume_state_path,
                    ArchiveResumeState(
                        mode="archive",
                        sort=sort,
                        page_size=page_size,
                        shard_index=shard_index,
                        shard_count=shard_count,
                        next_page_number=page_number + 1,
                        next_cursor=cursor,
                        completed=not bool(cursor),
                    ),
                )
                LOGGER.info(
                    "Archive page %s: shard_received=0, advancing cursor=%s",
                    page_number,
                    "yes" if cursor else "no",
                )
                if not cursor:
                    break
                continue

            LOGGER.info(
                "Archive page %s: received %s skills from index, shard_received=%s, processed_so_far=%s",
                page_number,
                len(raw_items),
                len(items),
                summary.skills_processed,
            )

            results = await self._collect_archive_page_items(
                items,
                page_number=page_number,
                include_versions=include_versions,
                include_files=include_files,
                include_file_texts=include_file_texts,
                max_version_pages=max_version_pages,
                version_page_size=version_page_size,
                max_files_per_version=max_files_per_version,
            )
            for result in results:
                if result is None:
                    continue
                if result.bundle.metadata.get("archive_skipped"):
                    summary.skills_skipped += 1
                    continue
                summary.skills_processed += 1
                summary.skills_upserted += 1
                summary.versions_upserted += len(result.bundle.versions)
                summary.files_upserted += len(result.files)
                summary.file_texts_saved += result.file_texts_saved
                if result.bundle.publisher:
                    summary.publisher_records_upserted += 1
                if result.bundle.permission_hint:
                    summary.permission_hints_upserted += 1
                if result.bundle.skill.page_url and result.bundle.skill.page_url not in seen_urls:
                    seen_urls.add(result.bundle.skill.page_url)
                    summary.discovered_skill_urls.append(result.bundle.skill.page_url)

            if remaining is not None:
                remaining -= len([result for result in results if result is not None])
                if remaining <= 0:
                    break

            summary.next_cursor = cursor
            self._save_resume_state(
                resume_state_path,
                ArchiveResumeState(
                    mode="archive",
                    sort=sort,
                    page_size=page_size,
                    shard_index=shard_index,
                    shard_count=shard_count,
                    next_page_number=page_number + 1,
                    next_cursor=cursor,
                    completed=not bool(cursor),
                ),
            )
            LOGGER.info(
                "Archive page %s complete: skills=%s skipped=%s versions=%s files=%s text_files=%s hints=%s next_cursor=%s",
                page_number,
                summary.skills_processed,
                summary.skills_skipped,
                summary.versions_upserted,
                summary.files_upserted,
                summary.file_texts_saved,
                summary.permission_hints_upserted,
                "yes" if cursor else "no",
            )
            if not cursor:
                break

        return summary

    @staticmethod
    def _load_resume_state(
        state_path: Path | None,
        *,
        mode: str,
        sort: str,
        page_size: int,
        shard_index: int,
        shard_count: int,
    ) -> ArchiveResumeState | None:
        if state_path is None or not state_path.exists():
            return None
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive path
            LOGGER.warning("Failed to parse resume state %s: %s", state_path, exc)
            return None
        state = ArchiveResumeState.from_dict(payload)
        expected = (mode, sort, page_size, shard_index, shard_count)
        actual = (state.mode, state.sort, state.page_size, state.shard_index, state.shard_count)
        if actual != expected:
            LOGGER.warning(
                "Ignoring incompatible resume state %s: expected=%s actual=%s",
                state_path,
                expected,
                actual,
            )
            return None
        LOGGER.info(
            "Loaded resume state from %s: next_page=%s cursor=%s completed=%s",
            state_path,
            state.next_page_number,
            "yes" if state.next_cursor else "no",
            state.completed,
        )
        return state

    @staticmethod
    def _save_resume_state(
        state_path: Path | None,
        state: ArchiveResumeState,
    ) -> None:
        if state_path is None:
            return
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _filter_items_for_shard(
        items: list[dict[str, Any]],
        *,
        shard_index: int,
        shard_count: int,
    ) -> list[dict[str, Any]]:
        if shard_count <= 1:
            return items
        filtered: list[dict[str, Any]] = []
        for item in items:
            slug = str(item.get("slug") or "").strip()
            if not slug:
                continue
            if PublicApiCollector._belongs_to_shard(slug, shard_index=shard_index, shard_count=shard_count):
                filtered.append(item)
        return filtered

    @staticmethod
    def _belongs_to_shard(slug: str, *, shard_index: int, shard_count: int) -> bool:
        if shard_count <= 1:
            return True
        if shard_index < 0 or shard_index >= shard_count:
            raise ValueError(f"Invalid shard_index={shard_index} for shard_count={shard_count}")
        digest = hashlib.sha256(slug.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], byteorder="big", signed=False) % shard_count
        return bucket == shard_index

    async def collect_skill_detail_by_slug(
        self,
        slug: str,
        *,
        persist: bool = True,
    ) -> SkillParseBundle:
        api_url = self._build_detail_url(slug)
        payload = await self.fetcher.fetch_json(api_url)
        page_url = self._build_page_url(payload, slug)
        bundle = parse_skill_detail_from_api(payload, page_url, self.config.base_url)
        skill = self._merge_detail_payload(bundle.skill, payload)
        publisher = self._merge_publisher_payload(bundle.publisher, payload)
        versions = self._build_versions(bundle.versions, payload, slug, skill.skill_id)

        result = SkillParseBundle(
            skill=skill,
            versions=versions,
            publisher=publisher,
            metadata={"detail_api_url": api_url},
        )

        if persist and not self.config.dry_run:
            self.db.upsert_skill(result.skill)
            for version in result.versions:
                self.db.upsert_skill_version(version)
            if result.publisher:
                self.db.upsert_publisher(result.publisher)

        return result

    async def collect_skill_archive_by_slug(
        self,
        slug: str,
        *,
        index_item: dict[str, Any] | None = None,
        include_versions: bool = True,
        include_files: bool = True,
        include_file_texts: bool = True,
        max_version_pages: int | None = None,
        version_page_size: int = 100,
        max_files_per_version: int | None = None,
    ) -> SkillArchiveResult:
        index_updated_at = coerce_iso_datetime((index_item or {}).get("updatedAt"))
        index_latest_version = str((((index_item or {}).get("latestVersion") or {}).get("version") or "")).strip() or None
        index_versions_count = ((index_item or {}).get("stats") or {}).get("versions")

        if not self.db.skill_archive_needs_refresh(
            slug,
            include_versions=include_versions,
            include_files=include_files,
            include_file_texts=include_file_texts,
            updated_at=index_updated_at,
            current_version=index_latest_version,
            versions_count=index_versions_count,
        ):
            return SkillArchiveResult(
                bundle=SkillParseBundle(
                    skill=Skill(skill_id=f"<cached>/{slug}", page_url=f"{self.config.base_url}/skills/{slug}"),
                    metadata={"archive_skipped": True, "archive_skip_reason": "existing complete and unchanged"},
                )
            )
        bundle = await self.collect_skill_detail_by_slug(slug, persist=False)
        files: list[SkillFile] = []
        file_texts_saved = 0
        hint_texts = [bundle.skill.description or ""]

        if include_versions:
            version_result = await self.collect_all_versions_for_slug(
                slug,
                bundle.skill.skill_id,
                include_files=include_files,
                include_file_texts=include_file_texts,
                max_pages=max_version_pages,
                page_size=version_page_size,
                max_files_per_version=max_files_per_version,
            )
            bundle.versions = self._merge_versions_records(bundle.versions + version_result.versions)
            files = version_result.files
            file_texts_saved = version_result.file_texts_saved
            hint_texts.extend(
                version.changelog or "" for version in bundle.versions if version.changelog
            )
            hint_texts.extend(version_result.file_texts)

        permission_hint = self._build_permission_hint(bundle.skill.skill_id, hint_texts)
        bundle.permission_hint = permission_hint

        if not self.config.dry_run:
            self.db.upsert_skill(bundle.skill)
            for version in bundle.versions:
                self.db.upsert_skill_version(version)
            for file_record in files:
                self.db.upsert_skill_file(file_record)
            if bundle.publisher:
                self.db.upsert_publisher(bundle.publisher)
            if permission_hint:
                self.db.upsert_permission_hint(permission_hint)
            self.db.upsert_skill_archive_status(
                SkillArchiveStatus(
                    slug=slug,
                    skill_id=bundle.skill.skill_id,
                    detail_completed=True,
                    versions_completed=include_versions,
                    files_completed=include_files,
                    file_texts_completed=include_file_texts,
                    last_error=None,
                    last_attempt_at=now_utc_iso(),
                    completed_at=now_utc_iso(),
                )
            )

        return SkillArchiveResult(bundle=bundle, files=files, file_texts_saved=file_texts_saved)

    async def collect_all_versions_for_slug(
        self,
        slug: str,
        skill_id: str,
        *,
        include_files: bool = True,
        include_file_texts: bool = True,
        max_pages: int | None = None,
        page_size: int = 100,
        max_files_per_version: int | None = None,
    ) -> VersionCollectionResult:
        version_pages_limit = max_pages or self.config.api_index_max_pages
        cursor: str | None = None
        version_seed_records: list[SkillVersion] = []
        version_names: list[str] = []

        for _ in range(1, version_pages_limit + 1):
            payload = await self.fetcher.fetch_json(
                self._build_versions_url(slug, page_size=page_size, cursor=cursor)
            )
            items = payload.get("items") or []
            if not items:
                break
            for item in items:
                version_name = str(item.get("version") or "").strip()
                if not version_name or version_name in version_names:
                    continue
                version_names.append(version_name)
                version_seed_records.append(
                    SkillVersion(
                        skill_id=skill_id,
                        version=version_name,
                        release_time=coerce_iso_datetime(item.get("createdAt")),
                        changelog=clean_multiline_text(item.get("changelog")),
                        bundle_url=self._build_download_url(slug, version_name),
                    )
                )
            cursor = payload.get("nextCursor") or None
            if not cursor:
                break

        semaphore = asyncio.Semaphore(max(1, self.config.concurrent_requests))

        async def run(version_seed: SkillVersion) -> tuple[SkillVersion, list[SkillFile], list[str], int]:
            async with semaphore:
                return await self._collect_single_version(
                    slug,
                    version_seed,
                    include_files=include_files,
                    include_file_texts=include_file_texts,
                    max_files_per_version=max_files_per_version,
                )

        results = await asyncio.gather(*(run(seed) for seed in version_seed_records))
        merged_versions: list[SkillVersion] = []
        all_files: list[SkillFile] = []
        file_texts: list[str] = []
        file_texts_saved = 0
        for version, files, texts, saved in results:
            merged_versions.append(version)
            all_files.extend(files)
            file_texts.extend(texts)
            file_texts_saved += saved
        return VersionCollectionResult(
            versions=self._merge_versions_records(merged_versions),
            files=all_files,
            file_texts=file_texts,
            file_texts_saved=file_texts_saved,
        )

    async def _collect_page_items(self, items: list[dict[str, Any]]) -> list[SkillParseBundle | None]:
        semaphore = asyncio.Semaphore(max(1, self.config.concurrent_requests))

        async def run(item: dict[str, Any]) -> SkillParseBundle | None:
            slug = str(item.get("slug") or "").strip()
            if not slug:
                return None
            async with semaphore:
                try:
                    return await self.collect_skill_detail_by_slug(slug)
                except Exception as exc:  # pragma: no cover - network path
                    LOGGER.warning("API detail collection failed for %s: %s", slug, exc)
                    return None

        return await asyncio.gather(*(run(item) for item in items))

    async def _collect_archive_page_items(
        self,
        items: list[dict[str, Any]],
        *,
        page_number: int,
        include_versions: bool,
        include_files: bool,
        include_file_texts: bool,
        max_version_pages: int | None,
        version_page_size: int,
        max_files_per_version: int | None,
    ) -> list[SkillArchiveResult | None]:
        semaphore = asyncio.Semaphore(max(1, self.config.concurrent_requests))

        async def run(item: dict[str, Any]) -> tuple[str, SkillArchiveResult | None]:
            slug = str(item.get("slug") or "").strip()
            if not slug:
                return "", None
            async with semaphore:
                try:
                    result = await self.collect_skill_archive_by_slug(
                        slug,
                        index_item=item,
                        include_versions=include_versions,
                        include_files=include_files,
                        include_file_texts=include_file_texts,
                        max_version_pages=max_version_pages,
                        version_page_size=version_page_size,
                        max_files_per_version=max_files_per_version,
                    )
                    return slug, result
                except Exception as exc:  # pragma: no cover - network path
                    LOGGER.warning("API archive collection failed for %s: %s", slug, exc)
                    if not self.config.dry_run:
                        self.db.mark_skill_archive_failure(
                            slug=slug,
                            error=str(exc),
                            attempted_at=now_utc_iso(),
                        )
                    return slug, None

        tasks = [asyncio.create_task(run(item)) for item in items]
        results: list[SkillArchiveResult | None] = []
        total = len(tasks)
        completed = 0
        for task in asyncio.as_completed(tasks):
            slug, result = await task
            completed += 1
            results.append(result)
            if result is None:
                LOGGER.info(
                    "Archive page %s progress: %s/%s skills complete, last=%s failed",
                    page_number,
                    completed,
                    total,
                    slug or "<missing-slug>",
                )
                continue
            if result.bundle.metadata.get("archive_skipped"):
                LOGGER.info(
                    "Archive page %s progress: %s/%s skills complete, last=%s skipped(%s)",
                    page_number,
                    completed,
                    total,
                    slug,
                    result.bundle.metadata.get("archive_skip_reason", "existing complete"),
                )
                continue
            LOGGER.info(
                "Archive page %s progress: %s/%s skills complete, last=%s versions=%s files=%s text_files=%s",
                page_number,
                completed,
                total,
                slug,
                len(result.bundle.versions),
                len(result.files),
                result.file_texts_saved,
            )
        return results

    async def _collect_single_version(
        self,
        slug: str,
        version_seed: SkillVersion,
        *,
        include_files: bool,
        include_file_texts: bool,
        max_files_per_version: int | None,
    ) -> tuple[SkillVersion, list[SkillFile], list[str], int]:
        payload = await self.fetcher.fetch_json(
            self._build_version_detail_url(slug, version_seed.version)
        )
        detail_version, files = parse_version_detail_from_api(
            payload, version_seed.skill_id, self.config.public_api_base
        )
        merged_version = self._merge_version_pair(version_seed, detail_version or version_seed)
        merged_version.bundle_url = merge_optional(
            merged_version.bundle_url, self._build_download_url(slug, version_seed.version)
        )
        if not include_files:
            return merged_version, [], [], 0
        if max_files_per_version is not None:
            files = files[:max_files_per_version]
        if not include_file_texts:
            return merged_version, files, [], 0
        hydrated_files, file_texts, saved_count = await self._hydrate_file_texts(
            slug,
            version_seed.version,
            files,
        )
        return merged_version, hydrated_files, file_texts, saved_count

    async def _hydrate_file_texts(
        self,
        slug: str,
        version: str,
        files: list[SkillFile],
    ) -> tuple[list[SkillFile], list[str], int]:
        saved_count = 0
        extracted_texts: list[str] = []
        hydrated: list[SkillFile] = []
        semaphore = asyncio.Semaphore(max(1, self.config.concurrent_requests))

        async def run(file_record: SkillFile) -> SkillFile:
            async with semaphore:
                if not self._is_textual_file(file_record):
                    return file_record
                try:
                    text = await self.fetcher.fetch_text(
                        self._build_file_url(slug, version=version, path=file_record.path)
                    )
                except Exception as exc:  # pragma: no cover - network path
                    LOGGER.debug(
                        "Public file fetch failed for %s@%s %s: %s",
                        slug,
                        version,
                        file_record.path,
                        exc,
                    )
                    return file_record
                target = self._save_file_text_snapshot(slug, version, file_record.path, text)
                extracted_texts.append(text)
                return SkillFile(
                    skill_id=file_record.skill_id,
                    version=file_record.version,
                    path=file_record.path,
                    size=file_record.size,
                    sha256=file_record.sha256,
                    content_type=file_record.content_type,
                    text_path=str(target),
                    collected_at=file_record.collected_at,
                )

        for file_record in await asyncio.gather(*(run(file_record) for file_record in files)):
            if file_record.text_path:
                saved_count += 1
            hydrated.append(file_record)
        return hydrated, extracted_texts, saved_count

    def _save_file_text_snapshot(self, slug: str, version: str, path: str, text: str) -> Path:
        suffix = Path(path).suffix or ".txt"
        pseudo_url = self._build_file_url(slug, version=version, path=path)
        target = make_snapshot_path(
            self.config.snapshot_dir / "api_files",
            pseudo_url,
            suffix=suffix,
        )
        write_text_file(target, text)
        return target

    def _build_list_url(self, *, sort: str, page_size: int, cursor: str | None) -> str:
        params = {"limit": str(page_size), "sort": sort}
        if cursor:
            params["cursor"] = cursor
        return f"{self.config.public_api_base}{self.config.public_api_skills_path}?{urlencode(params)}"

    def _build_detail_url(self, slug: str) -> str:
        return f"{self.config.public_api_base}{self.config.public_api_skills_path}/{quote(slug, safe='')}"

    def _build_versions_url(self, slug: str, *, page_size: int, cursor: str | None) -> str:
        params = {"limit": str(page_size)}
        if cursor:
            params["cursor"] = cursor
        return (
            f"{self.config.public_api_base}{self.config.public_api_skills_path}/"
            f"{quote(slug, safe='')}/versions?{urlencode(params)}"
        )

    def _build_version_detail_url(self, slug: str, version: str) -> str:
        return (
            f"{self.config.public_api_base}{self.config.public_api_skills_path}/"
            f"{quote(slug, safe='')}/versions/{quote(version, safe='')}"
        )

    def _build_file_url(self, slug: str, *, version: str, path: str) -> str:
        params = {"path": path, "version": version}
        return (
            f"{self.config.public_api_base}{self.config.public_api_skills_path}/"
            f"{quote(slug, safe='')}/file?{urlencode(params)}"
        )

    def _build_download_url(self, slug: str, version: str) -> str:
        return (
            f"{self.config.public_api_base}/api/v1/download?"
            f"{urlencode({'slug': slug, 'version': version})}"
        )

    def _build_page_url(self, payload: dict[str, Any], slug: str) -> str:
        owner = payload.get("owner") or {}
        owner_segment = owner.get("handle") or owner.get("userId") or "unknown"
        return ensure_absolute_url(self.config.base_url, f"/{owner_segment}/{slug}") or (
            f"{self.config.base_url}/{owner_segment}/{slug}"
        )

    def _merge_detail_payload(self, skill: Skill, payload: dict[str, Any]) -> Skill:
        latest_version = payload.get("latestVersion") or {}
        metadata = payload.get("metadata") or {}
        tags = merge_string_lists(skill.tags, list(((payload.get("skill") or {}).get("tags") or {}).keys()))
        return Skill(
            skill_id=skill.skill_id,
            name=skill.name,
            page_url=skill.page_url,
            description=skill.description,
            author_handle=skill.author_handle,
            author_profile_url=skill.author_profile_url,
            stars=skill.stars,
            downloads=merge_optional(skill.downloads, (payload.get("skill") or {}).get("stats", {}).get("downloads")),
            installs_current=merge_optional(
                skill.installs_current, (payload.get("skill") or {}).get("stats", {}).get("installsCurrent")
            ),
            installs_all_time=merge_optional(
                skill.installs_all_time, (payload.get("skill") or {}).get("stats", {}).get("installsAllTime")
            ),
            comments_count=merge_optional(
                skill.comments_count, (payload.get("skill") or {}).get("stats", {}).get("comments")
            ),
            versions_count=merge_optional(
                skill.versions_count, (payload.get("skill") or {}).get("stats", {}).get("versions")
            ),
            is_highlighted=skill.is_highlighted,
            is_popular=skill.is_popular,
            is_official=skill.is_official or any(tag.lower() == "official" for tag in tags),
            is_deprecated=skill.is_deprecated or any(tag.lower() == "deprecated" for tag in tags),
            is_suspicious=skill.is_suspicious or bool((payload.get("moderation") or {}).get("isSuspicious")),
            is_malware_blocked=skill.is_malware_blocked or bool(
                (payload.get("moderation") or {}).get("isMalwareBlocked")
            ),
            install_command=skill.install_command,
            current_version=merge_optional(skill.current_version, latest_version.get("version")),
            created_at=skill.created_at,
            updated_at=skill.updated_at,
            scan_status=merge_optional(skill.scan_status, self._scan_status_from_payload(payload)),
            scan_report_url=merge_optional(skill.scan_report_url, self._scan_report_url_from_payload(payload)),
            category=merge_optional(skill.category, metadata.get("category")),
            tags=tags,
            metadata_os=merge_string_lists(skill.metadata_os, metadata.get("os") or []),
            metadata_systems=merge_string_lists(skill.metadata_systems, metadata.get("systems") or []),
            raw_html_path=skill.raw_html_path,
            collected_at=skill.collected_at,
        )

    def _merge_publisher_payload(
        self,
        publisher: Publisher | None,
        payload: dict[str, Any],
    ) -> Publisher | None:
        owner = payload.get("owner") or {}
        handle = owner.get("handle") or owner.get("userId")
        if not handle:
            return publisher
        profile_url = ensure_absolute_url(self.config.base_url, f"/u/{handle}") or (
            f"{self.config.base_url}/u/{handle}"
        )
        if publisher is None:
            return Publisher(
                author_handle=handle,
                profile_url=profile_url,
                display_name=owner.get("displayName"),
            )
        return Publisher(
            author_handle=publisher.author_handle,
            profile_url=publisher.profile_url or profile_url,
            display_name=merge_optional(publisher.display_name, owner.get("displayName")),
            bio=publisher.bio,
            num_skills=publisher.num_skills,
            total_stars=publisher.total_stars,
            total_downloads=publisher.total_downloads,
            collected_at=publisher.collected_at,
        )

    def _build_versions(
        self,
        seed_versions: list[SkillVersion],
        payload: dict[str, Any],
        slug: str,
        skill_id: str,
    ) -> list[SkillVersion]:
        latest_version = payload.get("latestVersion") or {}
        if not latest_version.get("version"):
            return seed_versions

        scan_status = self._scan_status_from_payload(payload)
        scan_report_url = self._scan_report_url_from_payload(payload)
        bundle_hash = latest_version.get("sha256hash")
        latest = SkillVersion(
            skill_id=skill_id,
            version=str(latest_version["version"]),
            release_time=seed_versions[0].release_time if seed_versions else None,
            changelog=seed_versions[0].changelog if seed_versions else None,
            bundle_url=self._build_download_url(slug, str(latest_version["version"])),
            bundle_hash=bundle_hash,
            scan_status=scan_status,
            scan_report_url=scan_report_url,
            files_count=len(latest_version.get("files") or []) or None,
        )
        merged: dict[str, SkillVersion] = {version.version: version for version in seed_versions}
        current = merged.get(latest.version)
        merged[latest.version] = self._merge_version_pair(current or latest, latest)
        return self._sort_versions(list(merged.values()))

    @staticmethod
    def _scan_status_from_payload(payload: dict[str, Any]) -> str | None:
        moderation = payload.get("moderation") or {}
        if moderation.get("isMalwareBlocked"):
            return "Malicious"
        if moderation.get("isSuspicious"):
            return "Suspicious"
        return None

    @staticmethod
    def _scan_report_url_from_payload(payload: dict[str, Any]) -> str | None:
        sha256_hash = (payload.get("latestVersion") or {}).get("sha256hash")
        if not sha256_hash:
            return None
        return f"https://www.virustotal.com/gui/file/{sha256_hash}"

    def _merge_versions_records(self, versions: list[SkillVersion]) -> list[SkillVersion]:
        merged: dict[str, SkillVersion] = {}
        for version in versions:
            current = merged.get(version.version)
            merged[version.version] = version if current is None else self._merge_version_pair(current, version)
        return self._sort_versions(list(merged.values()))

    @staticmethod
    def _merge_version_pair(base: SkillVersion, new: SkillVersion) -> SkillVersion:
        return SkillVersion(
            skill_id=base.skill_id,
            version=base.version,
            release_time=merge_optional(new.release_time, base.release_time),
            changelog=merge_optional(new.changelog, base.changelog),
            bundle_url=merge_optional(new.bundle_url, base.bundle_url),
            bundle_hash=merge_optional(new.bundle_hash, base.bundle_hash),
            manifest_hash=merge_optional(new.manifest_hash, base.manifest_hash),
            dependency_hash=merge_optional(new.dependency_hash, base.dependency_hash),
            scan_status=merge_optional(new.scan_status, base.scan_status),
            scan_report_url=merge_optional(new.scan_report_url, base.scan_report_url),
            security_has_warnings=base.security_has_warnings or new.security_has_warnings,
            security_checked_at=merge_optional(new.security_checked_at, base.security_checked_at),
            security_model=merge_optional(new.security_model, base.security_model),
            files_count=merge_optional(new.files_count, base.files_count),
            collected_at=new.collected_at,
        )

    @staticmethod
    def _sort_versions(versions: list[SkillVersion]) -> list[SkillVersion]:
        return sorted(
            versions,
            key=lambda item: (item.release_time or "", item.version),
            reverse=True,
        )

    @staticmethod
    def _build_permission_hint(skill_id: str, texts: list[str]) -> PermissionOrSecretHint | None:
        hint = extract_permission_hints(skill_id, texts)
        if not hint.raw_evidence:
            return None
        return hint

    @staticmethod
    def _is_textual_file(file_record: SkillFile) -> bool:
        content_type = (file_record.content_type or "").lower()
        if content_type.startswith("text/"):
            return True
        if any(
            token in content_type
            for token in ("json", "yaml", "xml", "javascript", "typescript", "shellscript")
        ):
            return True
        suffix = Path(file_record.path).suffix.lower()
        return suffix in _TEXT_FILE_SUFFIXES or file_record.path.upper().endswith("SKILL.MD")
