"""Search result collection."""

from __future__ import annotations

import logging
from urllib.parse import quote

from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.db import Database
from clawhub_analyzer.fetcher import Fetcher
from clawhub_analyzer.models import SearchResult, Skill, SkillCard
from clawhub_analyzer.parser import parse_browse_page, parse_search_results

LOGGER = logging.getLogger(__name__)


class SearchCollector:
    def __init__(self, config: AppConfig, db: Database, fetcher: Fetcher):
        self.config = config
        self.db = db
        self.fetcher = fetcher

    async def collect_query(self, query: str, *, top_k: int | None = None) -> list[SearchResult]:
        url = f"{self.config.base_url}{self.config.skills_path}?q={quote(query)}"
        html = await self.fetcher.fetch_page(
            url,
            wait_for_selector=".skills-row, .skill-card",
        )
        if self.config.use_playwright and (top_k or 0) > 25:
            html = await self.fetcher.fetch_rendered_html(
                url,
                wait_for_selector=".skills-row, .skill-card",
                scroll_steps=max(self.config.search_scroll_steps, ((top_k or 25) // 25) + 1),
            )
        self.fetcher.save_snapshot(url, html, label="search")
        cards = parse_browse_page(html, self.config.base_url)
        results = parse_search_results(html, query, self.config.base_url)
        if top_k is not None:
            results = results[:top_k]
            cards = cards[:top_k]
        if not self.config.dry_run:
            self.db.insert_search_results(results)
            for card in cards:
                self.db.upsert_skill(self._card_to_skill(card))
        LOGGER.info("Collected %s search results for query=%s", len(results), query)
        return results

    async def collect_many(self, queries: list[str], *, top_k: int | None = None) -> list[SearchResult]:
        all_results: list[SearchResult] = []
        for query in queries:
            all_results.extend(await self.collect_query(query, top_k=top_k))
        return all_results

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
