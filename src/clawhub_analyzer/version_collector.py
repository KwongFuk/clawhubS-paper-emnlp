"""Version history collection."""

from __future__ import annotations

import logging

from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.db import Database
from clawhub_analyzer.fetcher import Fetcher
from clawhub_analyzer.models import SkillVersion
from clawhub_analyzer.parser import parse_version_history

LOGGER = logging.getLogger(__name__)


class VersionCollector:
    def __init__(self, config: AppConfig, db: Database, fetcher: Fetcher):
        self.config = config
        self.db = db
        self.fetcher = fetcher

    async def collect(self, skill_url: str, skill_id: str) -> list[SkillVersion]:
        html = ""
        try:
            if self.config.use_playwright:
                html = await self.fetcher.render_page(
                    skill_url,
                    click_texts=["Versions"],
                    wait_for_selector=".version-row",
                )
            else:
                html = await self.fetcher.fetch_page(skill_url)
        except Exception as exc:
            LOGGER.warning("Version collection failed for %s: %s", skill_url, exc)
            return []
        versions = parse_version_history(html, skill_id)
        if not versions:
            return []
        self.fetcher.save_snapshot(skill_url, html, label="versions")
        if not self.config.dry_run:
            for version in versions:
                self.db.upsert_skill_version(version)
        return versions

    # TODO: future experiment hook for version diff analysis.
    async def collect_diff_candidates(self, skill_id: str) -> list[SkillVersion]:
        return []
