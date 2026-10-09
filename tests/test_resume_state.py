import asyncio
from pathlib import Path

from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.db import Database
from clawhub_analyzer.fetcher import Fetcher
from clawhub_analyzer.models import SkillParseBundle, Skill
from clawhub_analyzer.public_api_collector import ArchiveResumeState, PublicApiCollector


def test_resume_state_roundtrip(tmp_path: Path) -> None:
    state_path = tmp_path / "archive.state.json"
    state = ArchiveResumeState(
        mode="archive",
        sort="downloads",
        page_size=200,
        shard_index=1,
        shard_count=2,
        next_page_number=89,
        next_cursor="cursor-abc",
        completed=False,
    )

    PublicApiCollector._save_resume_state(state_path, state)
    loaded = PublicApiCollector._load_resume_state(
        state_path,
        mode="archive",
        sort="downloads",
        page_size=200,
        shard_index=1,
        shard_count=2,
    )

    assert loaded is not None
    assert loaded.next_page_number == 89
    assert loaded.next_cursor == "cursor-abc"
    assert loaded.completed is False


def test_resume_state_mismatch_is_ignored(tmp_path: Path) -> None:
    state_path = tmp_path / "archive.state.json"
    PublicApiCollector._save_resume_state(
        state_path,
        ArchiveResumeState(
            mode="archive",
            sort="downloads",
            page_size=200,
            shard_index=0,
            shard_count=2,
            next_page_number=10,
            next_cursor="cursor-xyz",
            completed=False,
        ),
    )

    loaded = PublicApiCollector._load_resume_state(
        state_path,
        mode="archive",
        sort="downloads",
        page_size=200,
        shard_index=1,
        shard_count=2,
    )

    assert loaded is None


class _DummyFetcher(Fetcher):
    def __init__(self) -> None:  # pragma: no cover - not used directly
        pass


class _ShardAwareCollector(PublicApiCollector):
    def __init__(self, config: AppConfig, db: Database, payloads: list[dict]) -> None:
        super().__init__(config, db, _DummyFetcher())
        self._payloads = payloads

    async def _fetch_index_payload_with_recovery(self, **_: object) -> dict:
        if not self._payloads:
            raise AssertionError("No more payloads queued")
        return self._payloads.pop(0)

    async def _collect_archive_page_items(self, items: list[dict], **_: object) -> list:
        results = []
        for item in items:
            slug = str(item["slug"])
            bundle = SkillParseBundle(
                skill=Skill(
                    skill_id=f"owner/{slug}",
                    page_url=f"https://clawhub.ai/owner/{slug}",
                    name=slug,
                ),
                versions=[],
                publisher=None,
                permission_hint=None,
            )
            results.append(type("Result", (), {"bundle": bundle, "files": [], "file_texts_saved": 0})())
        return results


def _find_slug_for_shard(target_shard: int, shard_count: int = 2) -> str:
    for idx in range(1000):
        slug = f"slug-{idx}"
        if PublicApiCollector._belongs_to_shard(
            slug,
            shard_index=target_shard,
            shard_count=shard_count,
        ):
            return slug
    raise AssertionError("Failed to find test slug for shard")


def test_archive_resume_skips_empty_shard_pages_and_continues(tmp_path: Path) -> None:
    state_path = tmp_path / "archive.state.json"
    db = Database(tmp_path / "test.sqlite3")
    try:
        config = AppConfig(dry_run=True, api_index_max_pages=10)
        shard0_slug = _find_slug_for_shard(0)
        shard1_slug = _find_slug_for_shard(1)
        collector = _ShardAwareCollector(
            config,
            db,
            payloads=[
                {
                    "items": [{"slug": shard1_slug}],
                    "nextCursor": "cursor-2",
                },
                {
                    "items": [{"slug": shard0_slug}],
                    "nextCursor": None,
                },
            ],
        )

        summary = asyncio.run(
            collector.collect_public_archive(
                sort="downloads",
                page_size=200,
                shard_index=0,
                shard_count=2,
                include_versions=False,
                include_files=False,
                include_file_texts=False,
                resume_state_path=state_path,
            )
        )

        loaded = PublicApiCollector._load_resume_state(
            state_path,
            mode="archive",
            sort="downloads",
            page_size=200,
            shard_index=0,
            shard_count=2,
        )

        assert summary.pages_fetched == 2
        assert summary.skills_processed == 1
        assert loaded is not None
        assert loaded.completed is True
    finally:
        db.close()


def test_archive_resume_skips_empty_raw_pages_and_continues(tmp_path: Path) -> None:
    state_path = tmp_path / "archive.state.json"
    db = Database(tmp_path / "test.sqlite3")
    try:
        config = AppConfig(dry_run=True, api_index_max_pages=10)
        shard0_slug = _find_slug_for_shard(0)
        collector = _ShardAwareCollector(
            config,
            db,
            payloads=[
                {
                    "items": [],
                    "nextCursor": "cursor-2",
                },
                {
                    "items": [{"slug": shard0_slug}],
                    "nextCursor": None,
                },
            ],
        )

        summary = asyncio.run(
            collector.collect_public_archive(
                sort="downloads",
                page_size=200,
                shard_index=0,
                shard_count=2,
                include_versions=False,
                include_files=False,
                include_file_texts=False,
                resume_state_path=state_path,
            )
        )

        loaded = PublicApiCollector._load_resume_state(
            state_path,
            mode="archive",
            sort="downloads",
            page_size=200,
            shard_index=0,
            shard_count=2,
        )

        assert summary.pages_fetched == 2
        assert summary.skills_processed == 1
        assert loaded is not None
        assert loaded.completed is True
    finally:
        db.close()
