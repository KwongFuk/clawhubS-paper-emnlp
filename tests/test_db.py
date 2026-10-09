from pathlib import Path

from clawhub_analyzer.db import Database
from clawhub_analyzer.models import (
    PermissionOrSecretHint,
    Skill,
    SkillArchiveStatus,
    SkillComment,
    SkillFile,
    SkillVersion,
)


def test_sqlite_upsert_skill_and_hint(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    try:
        first = Skill(
            skill_id="alice/slack-helper",
            page_url="https://clawhub.ai/alice/slack-helper",
            name="Slack Helper",
            downloads=100,
            tags=["Slack"],
        )
        second = Skill(
            skill_id="alice/slack-helper",
            page_url="https://clawhub.ai/alice/slack-helper",
            stars=5,
            is_highlighted=True,
            tags=["Email"],
        )
        db.upsert_skill(first)
        merged = db.upsert_skill(second)
        assert merged.name == "Slack Helper"
        assert merged.downloads == 100
        assert merged.stars == 5
        assert merged.is_highlighted is True
        assert sorted(merged.tags) == ["Email", "Slack"]

        hint = PermissionOrSecretHint(
            skill_id="alice/slack-helper",
            requires_slack=True,
            raw_evidence=["Requires Slack OAuth"],
        )
        hint2 = PermissionOrSecretHint(
            skill_id="alice/slack-helper",
            requires_token=True,
            raw_evidence=["Needs access token"],
        )
        db.upsert_permission_hint(hint)
        db.upsert_permission_hint(hint2)
        stored = db.get_permission_hint("alice/slack-helper")
        assert stored is not None
        assert stored.requires_slack is True
        assert stored.requires_token is True
        assert len(stored.raw_evidence) == 2

        file_record = SkillFile(
            skill_id="alice/slack-helper",
            version="1.0.0",
            path="SKILL.md",
            size=512,
            sha256="deadbeef",
            text_path="/tmp/SKILL.md",
        )
        db.upsert_skill_file(file_record)
        files = db.list_skill_files()
        assert len(files) == 1
        assert files[0].path == "SKILL.md"

        comments = [
            SkillComment(
                skill_id="alice/slack-helper",
                comment_index=1,
                author_handle="bob",
                body="Needs a Slack token",
            )
        ]
        db.insert_skill_comments(comments)
        stored_comments = db.list_skill_comments()
        assert len(stored_comments) == 1
        assert stored_comments[0].author_handle == "bob"

        status = SkillArchiveStatus(
            slug="slack-helper",
            skill_id="alice/slack-helper",
            detail_completed=True,
            versions_completed=True,
            files_completed=True,
            file_texts_completed=True,
            completed_at="2026-03-08T00:00:00Z",
        )
        db.upsert_skill_archive_status(status)
        assert db.skill_archive_is_complete(
            "slack-helper",
            include_versions=True,
            include_files=True,
            include_file_texts=True,
        )

        db2 = Database(tmp_path / "bootstrap.sqlite3")
        try:
            db2.upsert_skill(
                Skill(
                    skill_id="alice/bootstrap",
                    page_url="https://clawhub.ai/alice/bootstrap",
                    name="Bootstrap",
                )
            )
            db2.upsert_skill_file(
                SkillFile(
                    skill_id="alice/bootstrap",
                    version="1.0.0",
                    path="README.md",
                    text_path="/tmp/README.md",
                )
            )
            db2.upsert_skill_version(
                SkillVersion(skill_id="alice/bootstrap", version="1.0.0")
            )
            db2.bootstrap_skill_archive_statuses()
            assert db2.skill_archive_is_complete(
                "bootstrap",
                include_versions=True,
                include_files=True,
                include_file_texts=True,
            )
        finally:
            db2.close()
    finally:
        db.close()


def test_skill_archive_needs_refresh_when_index_metadata_changes(tmp_path: Path) -> None:
    db = Database(tmp_path / "refresh.sqlite3")
    try:
        db.upsert_skill(
            Skill(
                skill_id="alice/slack-helper",
                page_url="https://clawhub.ai/alice/slack-helper",
                name="Slack Helper",
                updated_at="2026-03-10T00:00:00Z",
                current_version="1.0.0",
                versions_count=2,
            )
        )
        db.upsert_skill_archive_status(
            SkillArchiveStatus(
                slug="slack-helper",
                skill_id="alice/slack-helper",
                detail_completed=True,
                versions_completed=True,
                files_completed=True,
                file_texts_completed=True,
                completed_at="2026-03-10T00:10:00Z",
            )
        )

        assert db.skill_archive_needs_refresh(
            "slack-helper",
            include_versions=True,
            include_files=True,
            include_file_texts=True,
            updated_at="2026-03-10T00:00:00Z",
            current_version="1.0.0",
            versions_count=2,
        ) is False

        assert db.skill_archive_needs_refresh(
            "slack-helper",
            include_versions=True,
            include_files=True,
            include_file_texts=True,
            updated_at="2026-03-11T00:00:00Z",
            current_version="1.0.0",
            versions_count=2,
        ) is True

        assert db.skill_archive_needs_refresh(
            "slack-helper",
            include_versions=True,
            include_files=True,
            include_file_texts=True,
            updated_at="2026-03-10T00:00:00Z",
            current_version="1.1.0",
            versions_count=2,
        ) is True

        assert db.skill_archive_needs_refresh(
            "slack-helper",
            include_versions=True,
            include_files=True,
            include_file_texts=True,
            updated_at="2026-03-10T00:00:00Z",
            current_version="1.0.0",
            versions_count=3,
        ) is True
    finally:
        db.close()
