"""Top-level crawl orchestration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable

from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.db import Database
from clawhub_analyzer.fetcher import Fetcher
from clawhub_analyzer.models import HomeParseResult, Skill, SkillCard
from clawhub_analyzer.parser import parse_browse_page, parse_homepage
from clawhub_analyzer.publisher_collector import PublisherCollector
from clawhub_analyzer.public_api_collector import ApiArchiveSummary, ApiIndexSummary, PublicApiCollector
from clawhub_analyzer.search_collector import SearchCollector
from clawhub_analyzer.skill_collector import SkillCollector
from clawhub_analyzer.utils import unique_list
from clawhub_analyzer.utils import append_jsonl
from clawhub_analyzer.version_collector import VersionCollector

LOGGER = logging.getLogger(__name__)


@dataclass
class CrawlSummary:
    indexed_skill_urls: list[str] = field(default_factory=list)
    discovered_skill_urls: list[str] = field(default_factory=list)
    collected_skills: list[str] = field(default_factory=list)
    publisher_urls: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    index_pages_fetched: int = 0
    index_skills_upserted: int = 0


@dataclass
class PublicArchiveRunSummary:
    api_summary: ApiArchiveSummary
    html_skill_pages_collected: int = 0
    publisher_pages_collected: int = 0


class Crawler:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db
        self.fetcher = Fetcher(config)
        self.public_api_collector = PublicApiCollector(config, db, self.fetcher)
        self.search_collector = SearchCollector(config, db, self.fetcher)
        self.skill_collector = SkillCollector(config, db, self.fetcher)
        self.publisher_collector = PublisherCollector(config, db, self.fetcher)
        self.version_collector = VersionCollector(config, db, self.fetcher)

    async def close(self) -> None:
        await self.fetcher.close()

    async def collect_home(self) -> HomeParseResult:
        url = f"{self.config.base_url}/"
        html = await self.fetcher.fetch_page(url, wait_for_selector=".skill-card")
        self.fetcher.save_snapshot(url, html, label="home")
        result = parse_homepage(html, self.config.base_url)
        if not self.config.dry_run:
            for card in result.highlighted_skills + result.popular_skills:
                self.db.upsert_skill(self._card_to_skill(card))
        return result

    async def collect_browse(self) -> list[SkillCard]:
        url = f"{self.config.base_url}{self.config.skills_path}?sort=downloads&dir=desc"
        html = await self.fetcher.fetch_page(url, wait_for_selector=".skills-row, .skill-card")
        if self.config.use_playwright:
            html = await self.fetcher.fetch_rendered_html(
                url,
                wait_for_selector=".skills-row, .skill-card",
                scroll_steps=self.config.browse_scroll_steps,
            )
        self.fetcher.save_snapshot(url, html, label="browse")
        cards = parse_browse_page(html, self.config.base_url)
        if not self.config.dry_run:
            for card in cards:
                self.db.upsert_skill(self._card_to_skill(card))
        return cards

    async def collect_index(
        self,
        *,
        sort: str | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
        shard_index: int = 0,
        shard_count: int = 1,
        resume_state_path=None,
    ) -> ApiIndexSummary:
        return await self.public_api_collector.collect_skill_index(
            sort=sort,
            page_size=page_size,
            max_pages=max_pages,
            shard_index=shard_index,
            shard_count=shard_count,
            resume_state_path=resume_state_path,
        )

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
        include_html_details: bool = False,
        max_version_pages: int | None = None,
        version_page_size: int = 100,
        max_files_per_version: int | None = None,
        resume_state_path=None,
    ) -> PublicArchiveRunSummary:
        api_summary = await self.public_api_collector.collect_public_archive(
            sort=sort,
            page_size=page_size,
            max_pages=max_pages,
            max_skills=max_skills,
            shard_index=shard_index,
            shard_count=shard_count,
            include_versions=include_versions,
            include_files=include_files,
            include_file_texts=include_file_texts,
            max_version_pages=max_version_pages,
            version_page_size=version_page_size,
            max_files_per_version=max_files_per_version,
            resume_state_path=resume_state_path,
        )
        summary = PublicArchiveRunSummary(api_summary=api_summary)
        if not include_html_details:
            return summary

        skill_urls = api_summary.discovered_skill_urls
        if max_skills is not None:
            skill_urls = skill_urls[:max_skills]
        bundles = await self._map_limited(skill_urls, self._collect_skill_safe)
        publisher_urls: list[str] = []
        for bundle in bundles:
            if not bundle:
                continue
            summary.html_skill_pages_collected += 1
            if bundle.skill.author_profile_url:
                publisher_urls.append(bundle.skill.author_profile_url)
        publisher_urls = unique_list(publisher_urls)
        results = await self._map_limited(publisher_urls, self._collect_publisher_safe)
        summary.publisher_pages_collected = len([result for result in results if result is not None])
        return summary

    async def crawl(
        self,
        *,
        max_pages: int | None = None,
        use_full_index: bool = False,
        index_max_pages: int | None = None,
    ) -> CrawlSummary:
        limit = max_pages or self.config.max_pages
        summary = CrawlSummary()
        home = await self.collect_home()
        browse_urls: list[str]
        if use_full_index:
            index_summary = await self.collect_index(
                sort=self.config.api_index_sort,
                page_size=self.config.api_page_size,
                max_pages=index_max_pages,
            )
            summary.indexed_skill_urls = index_summary.discovered_skill_urls
            summary.index_pages_fetched = index_summary.pages_fetched
            summary.index_skills_upserted = index_summary.skills_upserted
            browse_urls = index_summary.discovered_skill_urls
        else:
            browse = await self.collect_browse()
            browse_urls = [card.page_url for card in browse]
        search_results = await self.search_collector.collect_many(
            self.config.search_queries,
            top_k=self.config.search_top_k,
        )

        highlighted_urls = [card.page_url for card in home.highlighted_skills]
        popular_urls = [card.page_url for card in home.popular_skills]
        search_urls = [result.result_url for result in search_results]
        summary.discovered_skill_urls = unique_list(
            highlighted_urls + popular_urls + browse_urls + search_urls
        )

        selected_urls = summary.discovered_skill_urls[:limit]
        bundles = await self._map_limited(selected_urls, self._collect_skill_safe)
        publisher_urls: list[str] = []
        for bundle in bundles:
            if not bundle:
                continue
            summary.collected_skills.append(bundle.skill.skill_id)
            if bundle.skill.author_profile_url:
                publisher_urls.append(bundle.skill.author_profile_url)
            if self.config.use_playwright:
                await self.version_collector.collect(bundle.skill.page_url, bundle.skill.skill_id)
        publisher_urls = list(dict.fromkeys(publisher_urls))
        summary.publisher_urls = publisher_urls
        await self._map_limited(publisher_urls, self._collect_publisher_safe)
        return summary

    async def _collect_skill_safe(self, url: str):
        try:
            existing = self.db.get_skill_by_page_url(url)
            bundle = await self.skill_collector.collect(
                url,
                is_highlighted=existing.is_highlighted if existing else None,
                is_popular=existing.is_popular if existing else None,
            )
            return bundle
        except Exception as exc:
            self.record_failure(url, "skill", exc)
            return None

    async def _collect_publisher_safe(self, url: str):
        try:
            return await self.publisher_collector.collect(url)
        except Exception as exc:
            self.record_failure(url, "publisher", exc)
            return None

    async def _map_limited(self, items: Iterable[str], handler: Callable[[str], Awaitable]):
        semaphore = asyncio.Semaphore(max(1, self.config.concurrent_requests))

        async def run(item: str):
            async with semaphore:
                return await handler(item)

        return await asyncio.gather(*(run(item) for item in items))

    def record_failure(self, url: str, stage: str, exc: Exception) -> None:
        LOGGER.error("%s collection failed for %s: %s", stage, url, exc)
        append_jsonl(
            self.config.failure_log_path,
            {"stage": stage, "url": url, "error": str(exc)},
        )

    @staticmethod
    def _card_to_skill(card: SkillCard) -> Skill:
        return Skill(
            skill_id=card.skill_id,
            name=card.name,
            page_url=card.page_url,
            description=card.description,
            author_handle=card.author_handle,
            author_profile_url=card.author_profile_url,
            stars=card.stars,
            downloads=card.downloads,
            is_highlighted=card.is_highlighted,
            is_popular=card.is_popular,
            tags=card.tags,
        )
