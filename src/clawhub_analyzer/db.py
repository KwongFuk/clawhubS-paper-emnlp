"""SQLite persistence layer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from clawhub_analyzer.models import (
    PermissionOrSecretHint,
    Publisher,
    SearchResult,
    SkillArchiveStatus,
    Skill,
    SkillComment,
    SkillFile,
    SkillVersion,
)
from clawhub_analyzer.utils import merge_optional, merge_string_lists


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _from_json(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)


class Database:
    """Thin SQLite wrapper with Python-side merge upserts."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.create_tables()

    def close(self) -> None:
        self.connection.close()

    def create_tables(self) -> None:
        cursor = self.connection.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS skills (
                skill_id TEXT PRIMARY KEY,
                name TEXT,
                page_url TEXT UNIQUE,
                description TEXT,
                author_handle TEXT,
                author_profile_url TEXT,
                stars INTEGER,
                downloads INTEGER,
                installs_current INTEGER,
                installs_all_time INTEGER,
                comments_count INTEGER,
                versions_count INTEGER,
                is_highlighted INTEGER,
                is_popular INTEGER,
                is_official INTEGER,
                is_deprecated INTEGER,
                is_suspicious INTEGER,
                is_malware_blocked INTEGER,
                install_command TEXT,
                current_version TEXT,
                created_at TEXT,
                updated_at TEXT,
                scan_status TEXT,
                scan_report_url TEXT,
                category TEXT,
                tags TEXT,
                metadata_os TEXT,
                metadata_systems TEXT,
                raw_html_path TEXT,
                collected_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skill_versions (
                skill_id TEXT NOT NULL,
                version TEXT NOT NULL,
                release_time TEXT,
                changelog TEXT,
                bundle_url TEXT,
                bundle_hash TEXT,
                manifest_hash TEXT,
                dependency_hash TEXT,
                scan_status TEXT,
                scan_report_url TEXT,
                security_has_warnings INTEGER,
                security_checked_at TEXT,
                security_model TEXT,
                files_count INTEGER,
                collected_at TEXT NOT NULL,
                PRIMARY KEY (skill_id, version)
            );

            CREATE TABLE IF NOT EXISTS publishers (
                author_handle TEXT PRIMARY KEY,
                profile_url TEXT UNIQUE,
                display_name TEXT,
                bio TEXT,
                num_skills INTEGER,
                total_stars INTEGER,
                total_downloads INTEGER,
                collected_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS search_results (
                query TEXT NOT NULL,
                rank INTEGER NOT NULL,
                skill_id TEXT NOT NULL,
                skill_name TEXT,
                result_url TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                PRIMARY KEY (query, rank, skill_id, timestamp)
            );

            CREATE TABLE IF NOT EXISTS permission_secret_hints (
                skill_id TEXT PRIMARY KEY,
                requires_api_key INTEGER NOT NULL,
                requires_oauth INTEGER NOT NULL,
                requires_token INTEGER NOT NULL,
                requires_local_file_access INTEGER NOT NULL,
                requires_shell_access INTEGER NOT NULL,
                requires_browser_access INTEGER NOT NULL,
                requires_github INTEGER NOT NULL,
                requires_slack INTEGER NOT NULL,
                requires_notion INTEGER NOT NULL,
                requires_calendar INTEGER NOT NULL,
                requires_email INTEGER NOT NULL,
                requires_docs INTEGER NOT NULL,
                requires_pdf INTEGER NOT NULL,
                raw_evidence TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skill_files (
                skill_id TEXT NOT NULL,
                version TEXT NOT NULL,
                path TEXT NOT NULL,
                size INTEGER,
                sha256 TEXT,
                content_type TEXT,
                text_path TEXT,
                collected_at TEXT NOT NULL,
                PRIMARY KEY (skill_id, version, path)
            );

            CREATE TABLE IF NOT EXISTS skill_comments (
                skill_id TEXT NOT NULL,
                comment_index INTEGER NOT NULL,
                author_handle TEXT,
                body TEXT,
                collected_at TEXT NOT NULL,
                PRIMARY KEY (skill_id, comment_index)
            );

            CREATE TABLE IF NOT EXISTS skill_archive_statuses (
                slug TEXT PRIMARY KEY,
                skill_id TEXT,
                detail_completed INTEGER NOT NULL DEFAULT 0,
                versions_completed INTEGER NOT NULL DEFAULT 0,
                files_completed INTEGER NOT NULL DEFAULT 0,
                file_texts_completed INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                last_attempt_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_skills_page_url ON skills(page_url);
            CREATE INDEX IF NOT EXISTS idx_skills_author ON skills(author_handle);
            CREATE INDEX IF NOT EXISTS idx_versions_skill ON skill_versions(skill_id);
            CREATE INDEX IF NOT EXISTS idx_search_query ON search_results(query);
            CREATE INDEX IF NOT EXISTS idx_skill_files_skill ON skill_files(skill_id);
            CREATE INDEX IF NOT EXISTS idx_skill_comments_skill ON skill_comments(skill_id);
            CREATE INDEX IF NOT EXISTS idx_archive_status_skill_id ON skill_archive_statuses(skill_id);
            """
        )
        self._ensure_column("skills", "installs_current", "INTEGER")
        self._ensure_column("skills", "installs_all_time", "INTEGER")
        self._ensure_column("skills", "comments_count", "INTEGER")
        self._ensure_column("skills", "versions_count", "INTEGER")
        self._ensure_column("skills", "is_official", "INTEGER DEFAULT 0")
        self._ensure_column("skills", "is_deprecated", "INTEGER DEFAULT 0")
        self._ensure_column("skills", "is_suspicious", "INTEGER DEFAULT 0")
        self._ensure_column("skills", "is_malware_blocked", "INTEGER DEFAULT 0")
        self._ensure_column("skills", "metadata_os", "TEXT")
        self._ensure_column("skills", "metadata_systems", "TEXT")
        self._ensure_column("skill_versions", "security_has_warnings", "INTEGER DEFAULT 0")
        self._ensure_column("skill_versions", "security_checked_at", "TEXT")
        self._ensure_column("skill_versions", "security_model", "TEXT")
        self._ensure_column("skill_versions", "files_count", "INTEGER")
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column in columns:
            return
        self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _fetchone(self, query: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
        cursor = self.connection.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()

    def get_skill_by_id(self, skill_id: str) -> Skill | None:
        row = self._fetchone("SELECT * FROM skills WHERE skill_id = ?", (skill_id,))
        return self._row_to_skill(row) if row else None

    def get_skill_by_page_url(self, page_url: str) -> Skill | None:
        row = self._fetchone("SELECT * FROM skills WHERE page_url = ?", (page_url,))
        return self._row_to_skill(row) if row else None

    def get_skill_by_slug(self, slug: str) -> Skill | None:
        status = self.get_skill_archive_status(slug)
        if status and status.skill_id:
            by_status = self.get_skill_by_id(status.skill_id)
            if by_status:
                return by_status
        row = self._fetchone(
            """
            SELECT * FROM skills
            WHERE page_url LIKE ? OR skill_id LIKE ?
            ORDER BY collected_at DESC
            LIMIT 1
            """,
            (f"%/{slug}", f"%/{slug}"),
        )
        return self._row_to_skill(row) if row else None

    def get_publisher(self, author_handle: str) -> Publisher | None:
        row = self._fetchone("SELECT * FROM publishers WHERE author_handle = ?", (author_handle,))
        return self._row_to_publisher(row) if row else None

    def get_permission_hint(self, skill_id: str) -> PermissionOrSecretHint | None:
        row = self._fetchone(
            "SELECT * FROM permission_secret_hints WHERE skill_id = ?",
            (skill_id,),
        )
        return self._row_to_permission_hint(row) if row else None

    def get_skill_archive_status(self, slug: str) -> SkillArchiveStatus | None:
        row = self._fetchone(
            "SELECT * FROM skill_archive_statuses WHERE slug = ?",
            (slug,),
        )
        return self._row_to_skill_archive_status(row) if row else None

    def upsert_skill(self, skill: Skill) -> Skill:
        existing = self.get_skill_by_id(skill.skill_id) or self.get_skill_by_page_url(skill.page_url)
        if existing:
            merged = Skill(
                skill_id=existing.skill_id,
                name=merge_optional(skill.name, existing.name),
                page_url=merge_optional(skill.page_url, existing.page_url),
                description=merge_optional(skill.description, existing.description),
                author_handle=merge_optional(skill.author_handle, existing.author_handle),
                author_profile_url=merge_optional(skill.author_profile_url, existing.author_profile_url),
                stars=merge_optional(skill.stars, existing.stars),
                downloads=merge_optional(skill.downloads, existing.downloads),
                installs_current=merge_optional(skill.installs_current, existing.installs_current),
                installs_all_time=merge_optional(skill.installs_all_time, existing.installs_all_time),
                comments_count=merge_optional(skill.comments_count, existing.comments_count),
                versions_count=merge_optional(skill.versions_count, existing.versions_count),
                is_highlighted=existing.is_highlighted or skill.is_highlighted,
                is_popular=existing.is_popular or skill.is_popular,
                is_official=existing.is_official or skill.is_official,
                is_deprecated=existing.is_deprecated or skill.is_deprecated,
                is_suspicious=existing.is_suspicious or skill.is_suspicious,
                is_malware_blocked=existing.is_malware_blocked or skill.is_malware_blocked,
                install_command=merge_optional(skill.install_command, existing.install_command),
                current_version=merge_optional(skill.current_version, existing.current_version),
                created_at=merge_optional(skill.created_at, existing.created_at),
                updated_at=merge_optional(skill.updated_at, existing.updated_at),
                scan_status=merge_optional(skill.scan_status, existing.scan_status),
                scan_report_url=merge_optional(skill.scan_report_url, existing.scan_report_url),
                category=merge_optional(skill.category, existing.category),
                tags=merge_string_lists(existing.tags, skill.tags),
                metadata_os=merge_string_lists(existing.metadata_os, skill.metadata_os),
                metadata_systems=merge_string_lists(existing.metadata_systems, skill.metadata_systems),
                raw_html_path=merge_optional(skill.raw_html_path, existing.raw_html_path),
                collected_at=skill.collected_at,
            )
        else:
            merged = skill

        self.connection.execute(
            """
            INSERT OR REPLACE INTO skills (
                skill_id, name, page_url, description, author_handle, author_profile_url,
                stars, downloads, installs_current, installs_all_time, comments_count, versions_count,
                is_highlighted, is_popular, is_official, is_deprecated, is_suspicious, is_malware_blocked,
                install_command, current_version, created_at, updated_at, scan_status, scan_report_url,
                category, tags, metadata_os, metadata_systems, raw_html_path, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merged.skill_id,
                merged.name,
                merged.page_url,
                merged.description,
                merged.author_handle,
                merged.author_profile_url,
                merged.stars,
                merged.downloads,
                merged.installs_current,
                merged.installs_all_time,
                merged.comments_count,
                merged.versions_count,
                int(merged.is_highlighted),
                int(merged.is_popular),
                int(merged.is_official),
                int(merged.is_deprecated),
                int(merged.is_suspicious),
                int(merged.is_malware_blocked),
                merged.install_command,
                merged.current_version,
                merged.created_at,
                merged.updated_at,
                merged.scan_status,
                merged.scan_report_url,
                merged.category,
                _to_json(merged.tags),
                _to_json(merged.metadata_os),
                _to_json(merged.metadata_systems),
                merged.raw_html_path,
                merged.collected_at,
            ),
        )
        self.connection.commit()
        return merged

    def upsert_skill_version(self, version: SkillVersion) -> SkillVersion:
        existing = self._fetchone(
            "SELECT * FROM skill_versions WHERE skill_id = ? AND version = ?",
            (version.skill_id, version.version),
        )
        merged = version
        if existing:
            current = self._row_to_skill_version(existing)
            merged = SkillVersion(
                skill_id=current.skill_id,
                version=current.version,
                release_time=merge_optional(version.release_time, current.release_time),
                changelog=merge_optional(version.changelog, current.changelog),
                bundle_url=merge_optional(version.bundle_url, current.bundle_url),
                bundle_hash=merge_optional(version.bundle_hash, current.bundle_hash),
                manifest_hash=merge_optional(version.manifest_hash, current.manifest_hash),
                dependency_hash=merge_optional(version.dependency_hash, current.dependency_hash),
                scan_status=merge_optional(version.scan_status, current.scan_status),
                scan_report_url=merge_optional(version.scan_report_url, current.scan_report_url),
                security_has_warnings=current.security_has_warnings or version.security_has_warnings,
                security_checked_at=merge_optional(version.security_checked_at, current.security_checked_at),
                security_model=merge_optional(version.security_model, current.security_model),
                files_count=merge_optional(version.files_count, current.files_count),
                collected_at=version.collected_at,
            )
        self.connection.execute(
            """
            INSERT OR REPLACE INTO skill_versions (
                skill_id, version, release_time, changelog, bundle_url, bundle_hash,
                manifest_hash, dependency_hash, scan_status, scan_report_url,
                security_has_warnings, security_checked_at, security_model, files_count, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merged.skill_id,
                merged.version,
                merged.release_time,
                merged.changelog,
                merged.bundle_url,
                merged.bundle_hash,
                merged.manifest_hash,
                merged.dependency_hash,
                merged.scan_status,
                merged.scan_report_url,
                int(merged.security_has_warnings),
                merged.security_checked_at,
                merged.security_model,
                merged.files_count,
                merged.collected_at,
            ),
        )
        self.connection.commit()
        return merged

    def upsert_skill_file(self, file_record: SkillFile) -> SkillFile:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO skill_files (
                skill_id, version, path, size, sha256, content_type, text_path, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_record.skill_id,
                file_record.version,
                file_record.path,
                file_record.size,
                file_record.sha256,
                file_record.content_type,
                file_record.text_path,
                file_record.collected_at,
            ),
        )
        self.connection.commit()
        return file_record

    def insert_skill_comments(self, comments: list[SkillComment]) -> int:
        cursor = self.connection.cursor()
        count = 0
        for comment in comments:
            cursor.execute(
                """
                INSERT OR REPLACE INTO skill_comments (
                    skill_id, comment_index, author_handle, body, collected_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    comment.skill_id,
                    comment.comment_index,
                    comment.author_handle,
                    comment.body,
                    comment.collected_at,
                ),
            )
            count += cursor.rowcount
        self.connection.commit()
        return count

    def upsert_skill_archive_status(self, status: SkillArchiveStatus) -> SkillArchiveStatus:
        existing = self.get_skill_archive_status(status.slug)
        merged = status
        if existing:
            merged = SkillArchiveStatus(
                slug=existing.slug,
                skill_id=merge_optional(status.skill_id, existing.skill_id),
                detail_completed=existing.detail_completed or status.detail_completed,
                versions_completed=existing.versions_completed or status.versions_completed,
                files_completed=existing.files_completed or status.files_completed,
                file_texts_completed=existing.file_texts_completed or status.file_texts_completed,
                last_error=status.last_error,
                last_attempt_at=status.last_attempt_at,
                completed_at=merge_optional(status.completed_at, existing.completed_at),
            )
        self.connection.execute(
            """
            INSERT OR REPLACE INTO skill_archive_statuses (
                slug, skill_id, detail_completed, versions_completed, files_completed,
                file_texts_completed, last_error, last_attempt_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merged.slug,
                merged.skill_id,
                int(merged.detail_completed),
                int(merged.versions_completed),
                int(merged.files_completed),
                int(merged.file_texts_completed),
                merged.last_error,
                merged.last_attempt_at,
                merged.completed_at,
            ),
        )
        self.connection.commit()
        return merged

    def mark_skill_archive_failure(
        self,
        *,
        slug: str,
        error: str,
        skill_id: str | None = None,
        attempted_at: str,
    ) -> SkillArchiveStatus:
        return self.upsert_skill_archive_status(
            SkillArchiveStatus(
                slug=slug,
                skill_id=skill_id,
                last_error=error,
                last_attempt_at=attempted_at,
            )
        )

    def bootstrap_skill_archive_statuses(self) -> int:
        source_rows = self.connection.execute(
            """
            SELECT
                CASE
                    WHEN instr(s.skill_id, '/') > 0 THEN substr(s.skill_id, instr(s.skill_id, '/') + 1)
                    ELSE s.skill_id
                END AS slug,
                s.skill_id AS skill_id,
                1 AS detail_completed,
                CASE WHEN EXISTS(
                    SELECT 1 FROM skill_versions sv WHERE sv.skill_id = s.skill_id
                ) THEN 1 ELSE 0 END AS versions_completed,
                CASE WHEN EXISTS(
                    SELECT 1 FROM skill_files sf WHERE sf.skill_id = s.skill_id
                ) THEN 1 ELSE 0 END AS files_completed,
                CASE WHEN EXISTS(
                    SELECT 1 FROM skill_files sf
                    WHERE sf.skill_id = s.skill_id
                      AND sf.text_path IS NOT NULL
                      AND sf.text_path != ''
                ) THEN 1 ELSE 0 END AS file_texts_completed,
                NULL AS last_error,
                s.collected_at AS last_attempt_at,
                s.collected_at AS completed_at
            FROM skills s
            """
        ).fetchall()
        existing_rows = {
            row["slug"]: row
            for row in self.connection.execute(
                "SELECT * FROM skill_archive_statuses"
            ).fetchall()
        }
        payloads: list[tuple[Any, ...]] = []
        for row in source_rows:
            existing = existing_rows.get(row["slug"])
            payloads.append(
                (
                    row["slug"],
                    row["skill_id"] if existing is None or existing["skill_id"] is None else existing["skill_id"],
                    max(int(existing["detail_completed"]) if existing else 0, int(row["detail_completed"])),
                    max(int(existing["versions_completed"]) if existing else 0, int(row["versions_completed"])),
                    max(int(existing["files_completed"]) if existing else 0, int(row["files_completed"])),
                    max(
                        int(existing["file_texts_completed"]) if existing else 0,
                        int(row["file_texts_completed"]),
                    ),
                    existing["last_error"] if existing else None,
                    row["last_attempt_at"],
                    existing["completed_at"] if existing and existing["completed_at"] else row["completed_at"],
                )
            )
        cursor = self.connection.cursor()
        cursor.executemany(
            """
            INSERT OR REPLACE INTO skill_archive_statuses (
                slug, skill_id, detail_completed, versions_completed, files_completed,
                file_texts_completed, last_error, last_attempt_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payloads,
        )
        self.connection.commit()
        return len(payloads)

    def skill_archive_is_complete(
        self,
        slug: str,
        *,
        include_versions: bool,
        include_files: bool,
        include_file_texts: bool,
    ) -> bool:
        status = self.get_skill_archive_status(slug)
        if status is None or not status.detail_completed:
            return False
        if include_versions and not status.versions_completed:
            return False
        if include_files and not status.files_completed:
            return False
        if include_file_texts and not status.file_texts_completed:
            return False
        return True

    def skill_archive_needs_refresh(
        self,
        slug: str,
        *,
        include_versions: bool,
        include_files: bool,
        include_file_texts: bool,
        updated_at: str | None = None,
        current_version: str | None = None,
        versions_count: int | None = None,
    ) -> bool:
        if not self.skill_archive_is_complete(
            slug,
            include_versions=include_versions,
            include_files=include_files,
            include_file_texts=include_file_texts,
        ):
            return True

        existing = self.get_skill_by_slug(slug)
        if existing is None:
            return True

        if updated_at and updated_at != existing.updated_at:
            return True
        if current_version and current_version != existing.current_version:
            return True
        if versions_count is not None and versions_count != existing.versions_count:
            return True
        return False

    def upsert_publisher(self, publisher: Publisher) -> Publisher:
        existing = self.get_publisher(publisher.author_handle)
        if existing:
            merged = Publisher(
                author_handle=existing.author_handle,
                profile_url=merge_optional(publisher.profile_url, existing.profile_url),
                display_name=merge_optional(publisher.display_name, existing.display_name),
                bio=merge_optional(publisher.bio, existing.bio),
                num_skills=merge_optional(publisher.num_skills, existing.num_skills),
                total_stars=merge_optional(publisher.total_stars, existing.total_stars),
                total_downloads=merge_optional(publisher.total_downloads, existing.total_downloads),
                collected_at=publisher.collected_at,
            )
        else:
            merged = publisher
        self.connection.execute(
            """
            INSERT OR REPLACE INTO publishers (
                author_handle, profile_url, display_name, bio, num_skills,
                total_stars, total_downloads, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merged.author_handle,
                merged.profile_url,
                merged.display_name,
                merged.bio,
                merged.num_skills,
                merged.total_stars,
                merged.total_downloads,
                merged.collected_at,
            ),
        )
        self.connection.commit()
        return merged

    def insert_search_results(self, results: list[SearchResult]) -> int:
        cursor = self.connection.cursor()
        count = 0
        for result in results:
            cursor.execute(
                """
                INSERT OR IGNORE INTO search_results (
                    query, rank, skill_id, skill_name, result_url, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    result.query,
                    result.rank,
                    result.skill_id,
                    result.skill_name,
                    result.result_url,
                    result.timestamp,
                ),
            )
            count += cursor.rowcount
        self.connection.commit()
        return count

    def upsert_permission_hint(self, hint: PermissionOrSecretHint) -> PermissionOrSecretHint:
        existing = self.get_permission_hint(hint.skill_id)
        if existing:
            merged = PermissionOrSecretHint(
                skill_id=hint.skill_id,
                requires_api_key=existing.requires_api_key or hint.requires_api_key,
                requires_oauth=existing.requires_oauth or hint.requires_oauth,
                requires_token=existing.requires_token or hint.requires_token,
                requires_local_file_access=existing.requires_local_file_access
                or hint.requires_local_file_access,
                requires_shell_access=existing.requires_shell_access or hint.requires_shell_access,
                requires_browser_access=existing.requires_browser_access or hint.requires_browser_access,
                requires_github=existing.requires_github or hint.requires_github,
                requires_slack=existing.requires_slack or hint.requires_slack,
                requires_notion=existing.requires_notion or hint.requires_notion,
                requires_calendar=existing.requires_calendar or hint.requires_calendar,
                requires_email=existing.requires_email or hint.requires_email,
                requires_docs=existing.requires_docs or hint.requires_docs,
                requires_pdf=existing.requires_pdf or hint.requires_pdf,
                raw_evidence=merge_string_lists(existing.raw_evidence, hint.raw_evidence),
            )
        else:
            merged = hint
        self.connection.execute(
            """
            INSERT OR REPLACE INTO permission_secret_hints (
                skill_id, requires_api_key, requires_oauth, requires_token,
                requires_local_file_access, requires_shell_access, requires_browser_access,
                requires_github, requires_slack, requires_notion, requires_calendar,
                requires_email, requires_docs, requires_pdf, raw_evidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merged.skill_id,
                int(merged.requires_api_key),
                int(merged.requires_oauth),
                int(merged.requires_token),
                int(merged.requires_local_file_access),
                int(merged.requires_shell_access),
                int(merged.requires_browser_access),
                int(merged.requires_github),
                int(merged.requires_slack),
                int(merged.requires_notion),
                int(merged.requires_calendar),
                int(merged.requires_email),
                int(merged.requires_docs),
                int(merged.requires_pdf),
                _to_json(merged.raw_evidence),
            ),
        )
        self.connection.commit()
        return merged

    def list_skills(self) -> list[Skill]:
        rows = self.connection.execute("SELECT * FROM skills ORDER BY page_url").fetchall()
        return [self._row_to_skill(row) for row in rows]

    def list_publishers(self) -> list[Publisher]:
        rows = self.connection.execute("SELECT * FROM publishers ORDER BY author_handle").fetchall()
        return [self._row_to_publisher(row) for row in rows]

    def list_skill_versions(self) -> list[SkillVersion]:
        rows = self.connection.execute(
            "SELECT * FROM skill_versions ORDER BY skill_id, version"
        ).fetchall()
        return [self._row_to_skill_version(row) for row in rows]

    def list_search_results(self) -> list[SearchResult]:
        rows = self.connection.execute(
            "SELECT * FROM search_results ORDER BY query, rank, timestamp"
        ).fetchall()
        return [self._row_to_search_result(row) for row in rows]

    def list_permission_hints(self) -> list[PermissionOrSecretHint]:
        rows = self.connection.execute(
            "SELECT * FROM permission_secret_hints ORDER BY skill_id"
        ).fetchall()
        return [self._row_to_permission_hint(row) for row in rows]

    def list_skill_files(self) -> list[SkillFile]:
        rows = self.connection.execute(
            "SELECT * FROM skill_files ORDER BY skill_id, version, path"
        ).fetchall()
        return [self._row_to_skill_file(row) for row in rows]

    def list_skill_comments(self) -> list[SkillComment]:
        rows = self.connection.execute(
            "SELECT * FROM skill_comments ORDER BY skill_id, comment_index"
        ).fetchall()
        return [self._row_to_skill_comment(row) for row in rows]

    def list_skill_archive_statuses(self) -> list[SkillArchiveStatus]:
        rows = self.connection.execute(
            "SELECT * FROM skill_archive_statuses ORDER BY slug"
        ).fetchall()
        return [self._row_to_skill_archive_status(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        cursor = self.connection.cursor()
        totals = cursor.execute(
            """
            SELECT
                COUNT(*) AS skill_count,
                SUM(CASE WHEN is_highlighted = 1 THEN 1 ELSE 0 END) AS highlighted_count,
                SUM(CASE WHEN is_popular = 1 THEN 1 ELSE 0 END) AS popular_count,
                AVG(stars) AS avg_stars,
                AVG(downloads) AS avg_downloads,
                SUM(CASE WHEN scan_status IS NOT NULL AND scan_status != '' THEN 1 ELSE 0 END) AS scan_status_count
            FROM skills
            """
        ).fetchone()
        publisher_count = cursor.execute("SELECT COUNT(*) FROM publishers").fetchone()[0]
        version_count = cursor.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0]
        file_count = cursor.execute("SELECT COUNT(*) FROM skill_files").fetchone()[0]
        text_file_count = cursor.execute(
            "SELECT COUNT(*) FROM skill_files WHERE text_path IS NOT NULL AND text_path != ''"
        ).fetchone()[0]
        comment_count = cursor.execute("SELECT COUNT(*) FROM skill_comments").fetchone()[0]
        permission_count = cursor.execute(
            """
            SELECT COUNT(*) FROM permission_secret_hints
            WHERE
                requires_api_key = 1 OR requires_oauth = 1 OR requires_token = 1 OR
                requires_local_file_access = 1 OR requires_shell_access = 1 OR
                requires_browser_access = 1 OR requires_github = 1 OR requires_slack = 1 OR
                requires_notion = 1 OR requires_calendar = 1 OR requires_email = 1 OR
                requires_docs = 1 OR requires_pdf = 1
            """
        ).fetchone()[0]
        return {
            "skill_count": totals["skill_count"] or 0,
            "publisher_count": publisher_count,
            "skill_version_count": version_count,
            "skill_file_count": file_count,
            "text_file_count": text_file_count,
            "skill_comment_count": comment_count,
            "highlighted_count": totals["highlighted_count"] or 0,
            "popular_count": totals["popular_count"] or 0,
            "avg_stars": float(totals["avg_stars"] or 0),
            "avg_downloads": float(totals["avg_downloads"] or 0),
            "scan_status_count": totals["scan_status_count"] or 0,
            "permission_hint_count": permission_count,
        }

    @staticmethod
    def _row_to_skill(row: sqlite3.Row) -> Skill:
        return Skill(
            skill_id=row["skill_id"],
            name=row["name"],
            page_url=row["page_url"],
            description=row["description"],
            author_handle=row["author_handle"],
            author_profile_url=row["author_profile_url"],
            stars=row["stars"],
            downloads=row["downloads"],
            installs_current=row["installs_current"],
            installs_all_time=row["installs_all_time"],
            comments_count=row["comments_count"],
            versions_count=row["versions_count"],
            is_highlighted=bool(row["is_highlighted"]),
            is_popular=bool(row["is_popular"]),
            is_official=bool(row["is_official"]),
            is_deprecated=bool(row["is_deprecated"]),
            is_suspicious=bool(row["is_suspicious"]),
            is_malware_blocked=bool(row["is_malware_blocked"]),
            install_command=row["install_command"],
            current_version=row["current_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            scan_status=row["scan_status"],
            scan_report_url=row["scan_report_url"],
            category=row["category"],
            tags=_from_json(row["tags"]) or [],
            metadata_os=_from_json(row["metadata_os"]) or [],
            metadata_systems=_from_json(row["metadata_systems"]) or [],
            raw_html_path=row["raw_html_path"],
            collected_at=row["collected_at"],
        )

    @staticmethod
    def _row_to_skill_version(row: sqlite3.Row) -> SkillVersion:
        return SkillVersion(
            skill_id=row["skill_id"],
            version=row["version"],
            release_time=row["release_time"],
            changelog=row["changelog"],
            bundle_url=row["bundle_url"],
            bundle_hash=row["bundle_hash"],
            manifest_hash=row["manifest_hash"],
            dependency_hash=row["dependency_hash"],
            scan_status=row["scan_status"],
            scan_report_url=row["scan_report_url"],
            security_has_warnings=bool(row["security_has_warnings"]),
            security_checked_at=row["security_checked_at"],
            security_model=row["security_model"],
            files_count=row["files_count"],
            collected_at=row["collected_at"],
        )

    @staticmethod
    def _row_to_skill_file(row: sqlite3.Row) -> SkillFile:
        return SkillFile(
            skill_id=row["skill_id"],
            version=row["version"],
            path=row["path"],
            size=row["size"],
            sha256=row["sha256"],
            content_type=row["content_type"],
            text_path=row["text_path"],
            collected_at=row["collected_at"],
        )

    @staticmethod
    def _row_to_skill_comment(row: sqlite3.Row) -> SkillComment:
        return SkillComment(
            skill_id=row["skill_id"],
            comment_index=row["comment_index"],
            author_handle=row["author_handle"],
            body=row["body"],
            collected_at=row["collected_at"],
        )

    @staticmethod
    def _row_to_skill_archive_status(row: sqlite3.Row) -> SkillArchiveStatus:
        return SkillArchiveStatus(
            slug=row["slug"],
            skill_id=row["skill_id"],
            detail_completed=bool(row["detail_completed"]),
            versions_completed=bool(row["versions_completed"]),
            files_completed=bool(row["files_completed"]),
            file_texts_completed=bool(row["file_texts_completed"]),
            last_error=row["last_error"],
            last_attempt_at=row["last_attempt_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _row_to_publisher(row: sqlite3.Row) -> Publisher:
        return Publisher(
            author_handle=row["author_handle"],
            profile_url=row["profile_url"],
            display_name=row["display_name"],
            bio=row["bio"],
            num_skills=row["num_skills"],
            total_stars=row["total_stars"],
            total_downloads=row["total_downloads"],
            collected_at=row["collected_at"],
        )

    @staticmethod
    def _row_to_search_result(row: sqlite3.Row) -> SearchResult:
        return SearchResult(
            query=row["query"],
            rank=row["rank"],
            skill_id=row["skill_id"],
            skill_name=row["skill_name"],
            result_url=row["result_url"],
            timestamp=row["timestamp"],
        )

    @staticmethod
    def _row_to_permission_hint(row: sqlite3.Row) -> PermissionOrSecretHint:
        return PermissionOrSecretHint(
            skill_id=row["skill_id"],
            requires_api_key=bool(row["requires_api_key"]),
            requires_oauth=bool(row["requires_oauth"]),
            requires_token=bool(row["requires_token"]),
            requires_local_file_access=bool(row["requires_local_file_access"]),
            requires_shell_access=bool(row["requires_shell_access"]),
            requires_browser_access=bool(row["requires_browser_access"]),
            requires_github=bool(row["requires_github"]),
            requires_slack=bool(row["requires_slack"]),
            requires_notion=bool(row["requires_notion"]),
            requires_calendar=bool(row["requires_calendar"]),
            requires_email=bool(row["requires_email"]),
            requires_docs=bool(row["requires_docs"]),
            requires_pdf=bool(row["requires_pdf"]),
            raw_evidence=_from_json(row["raw_evidence"]) or [],
        )
