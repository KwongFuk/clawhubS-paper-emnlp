"""Publisher profile collection."""

from __future__ import annotations

import logging

from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.db import Database
from clawhub_analyzer.fetcher import Fetcher
from clawhub_analyzer.models import PublisherParseResult
from clawhub_analyzer.parser import parse_publisher_page

LOGGER = logging.getLogger(__name__)


class PublisherCollector:
    def __init__(self, config: AppConfig, db: Database, fetcher: Fetcher):
        self.config = config
        self.db = db
        self.fetcher = fetcher

    async def collect(self, url: str) -> PublisherParseResult:
        html = await self.fetcher.fetch_page(url, wait_for_selector=".settings-name, .settings-handle")
        self.fetcher.save_snapshot(url, html, label="publishers")
        result = parse_publisher_page(html, url, self.config.base_url)
        if not self.config.dry_run:
            self.db.upsert_publisher(result.publisher)
        LOGGER.info("Collected publisher %s", result.publisher.author_handle)
        return result

    # TODO: future experiment hook for publisher similarity / impersonation analysis.
    def similarity_candidates(self) -> list[tuple[str, str]]:
        return []
