"""Resumable public archive collection for skills.sh."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import re
import sqlite3
import string
from typing import Iterable
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from bs4 import BeautifulSoup

from clawhub_analyzer.toxicity import HINT_COLUMNS


SEARCH_ALPHABET = string.ascii_lowercase + string.digits + "-"
SEARCH_RESULT_LIMIT = 1000
DEFAULT_TIMEOUT = 30
USER_AGENT = "clawhub-analyzer/0.1 (+skills.sh public archive)"
DEFAULT_PAGE_WORKERS = 8
DEFAULT_PROGRESS_EVERY = 50


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_url(url: str, *, timeout: int = DEFAULT_TIMEOUT) -> str:
    request = urllib_request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib_request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def log_progress(message: str) -> None:
    print(message, flush=True)


def parse_human_count(value: str | None) -> int:
    text = (value or "").strip().replace(",", "")
    if not text:
        return 0
    match = re.search(r"(\d+(?:\.\d+)?)([KMB]?)", text, flags=re.I)
    if not match:
        return 0
    number = float(match.group(1))
    suffix = match.group(2).upper()
    multiplier = 1
    if suffix == "K":
        multiplier = 1_000
    elif suffix == "M":
        multiplier = 1_000_000
    elif suffix == "B":
        multiplier = 1_000_000_000
    return int(number * multiplier)


def normalize_whitespace(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def slug_path_component(value: str) -> str:
    return urllib_parse.quote(value, safe="._-")


def _class_has(token: str):
    return lambda classes: bool(classes) and token in " ".join(classes if isinstance(classes, list) else [classes])


def _text_from_prose(node) -> str:
    if node is None:
        return ""
    return node.get_text("\n", strip=True)


def _find_labeled_metric_text(soup: BeautifulSoup, label: str) -> str:
    label_node = soup.find(string=lambda text: text and normalize_whitespace(text) == label)
    if label_node is None:
        return ""
    container = label_node.find_parent("div")
    if container is None:
        return ""
    value_node = container.find_next(
        "div",
        class_=lambda classes: bool(classes)
        and ("text-3xl" in " ".join(classes if isinstance(classes, list) else [classes])),
    )
    if value_node is None:
        return ""
    return normalize_whitespace(value_node.get_text(" ", strip=True))


def _find_section_prose_text(soup: BeautifulSoup, label: str) -> str:
    label_node = soup.find(string=lambda text: text and normalize_whitespace(text) == label)
    if label_node is None:
        return ""
    container = label_node.find_parent("div")
    if container is None:
        return ""
    prose = container.find_next("div", class_=_class_has("prose"))
    return _text_from_prose(prose)


def _extract_first_paragraph(text: str) -> str:
    for block in re.split(r"\n{2,}", text):
        candidate = normalize_whitespace(block)
        if candidate:
            return candidate
    return normalize_whitespace(text)


def _extract_github_url(soup: BeautifulSoup, *, owner: str | None = None, repo: str | None = None) -> str:
    prefix = "https://github.com/"
    if owner and repo:
        expected = f"{prefix}{owner}/{repo}"
        anchor = soup.find("a", href=expected)
        if anchor is not None:
            return expected
    if owner and repo is None:
        expected = f"{prefix}{owner}"
        anchor = soup.find("a", href=expected)
        if anchor is not None:
            return expected
    anchor = soup.find("a", href=lambda href: isinstance(href, str) and href.startswith(prefix))
    return anchor["href"] if anchor and anchor.has_attr("href") else ""


@dataclass(frozen=True)
class OwnerPageRecord:
    owner: str
    source_count: int
    skill_count: int
    total_installs: int
    github_url: str
    repos: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class RepoPageRecord:
    owner: str
    repo: str
    skill_count: int
    total_installs: int
    github_url: str
    install_command: str
    skills: tuple[tuple[str, str, int], ...]


@dataclass(frozen=True)
class SkillPageRecord:
    skill_id: str
    owner: str
    repo: str
    skill_slug: str
    name: str
    downloads: int
    repo_url: str
    install_command: str
    description: str
    summary_text: str
    skill_text: str


def parse_owner_page(html: str, owner: str) -> OwnerPageRecord:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    if main is None:
        raise ValueError(f"Owner page for {owner} has no <main>")
    text = main.get_text("\n", strip=True)
    source_count_match = re.search(r"(\d+)\s+sources", text)
    skill_count_match = re.search(r"(\d+)\s+skills", text)
    installs_match = re.search(r"([\d.]+[KMB]?)\s+total installs", text, flags=re.I)
    repos: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for anchor in main.find_all("a", href=True):
        href = anchor["href"]
        if not href.startswith(f"/{owner}/"):
            continue
        if href.count("/") != 2:
            continue
        repo_name = href.split("/", 2)[2]
        if not repo_name or repo_name in seen:
            continue
        paragraph = anchor.find("p")
        skill_count = 0
        if paragraph is not None:
            match = re.search(r"(\d+)\s+skills?", paragraph.get_text(" ", strip=True))
            if match:
                skill_count = int(match.group(1))
        count_text = ""
        for span in anchor.find_all("span"):
            candidate = normalize_whitespace(span.get_text(" ", strip=True))
            if re.fullmatch(r"\d+(?:\.\d+)?[KMB]?", candidate):
                count_text = candidate
        repos.append((repo_name, skill_count, parse_human_count(count_text)))
        seen.add(repo_name)
    return OwnerPageRecord(
        owner=owner,
        source_count=int(source_count_match.group(1)) if source_count_match else 0,
        skill_count=int(skill_count_match.group(1)) if skill_count_match else 0,
        total_installs=parse_human_count(installs_match.group(1) if installs_match else ""),
        github_url=_extract_github_url(soup, owner=owner),
        repos=tuple(repos),
    )


def parse_repo_page(html: str, owner: str, repo: str) -> RepoPageRecord:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    if main is None:
        raise ValueError(f"Repo page for {owner}/{repo} has no <main>")
    text = main.get_text("\n", strip=True)
    skill_count_match = re.search(r"(\d+)\s+skills", text)
    installs_match = re.search(r"([\d.]+[KMB]?)\s+total installs", text, flags=re.I)
    command_node = main.find("code")
    install_command = normalize_whitespace(command_node.get_text(" ", strip=True)) if command_node else ""
    skills: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    prefix = f"/{owner}/{repo}/"
    for anchor in main.find_all("a", href=True):
        href = anchor["href"]
        if not href.startswith(prefix):
            continue
        if href.count("/") != 3:
            continue
        slug = href.split("/", 3)[3]
        if not slug or slug in seen:
            continue
        title = anchor.find("h3")
        name = normalize_whitespace(title.get_text(" ", strip=True)) if title else slug
        count_text = ""
        for span in anchor.find_all("span"):
            candidate = normalize_whitespace(span.get_text(" ", strip=True))
            if re.fullmatch(r"\d+(?:\.\d+)?[KMB]?", candidate):
                count_text = candidate
        skills.append((slug, name, parse_human_count(count_text)))
        seen.add(slug)
    return RepoPageRecord(
        owner=owner,
        repo=repo,
        skill_count=int(skill_count_match.group(1)) if skill_count_match else len(skills),
        total_installs=parse_human_count(installs_match.group(1) if installs_match else ""),
        github_url=_extract_github_url(soup, owner=owner, repo=repo),
        install_command=install_command,
        skills=tuple(skills),
    )


def parse_skill_page(html: str, owner: str, repo: str, skill_slug: str) -> SkillPageRecord:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    if main is None:
        raise ValueError(f"Skill page for {owner}/{repo}/{skill_slug} has no <main>")
    h1 = main.find("h1")
    name = normalize_whitespace(h1.get_text(" ", strip=True)) if h1 else skill_slug
    code_candidates = main.find_all("code")
    install_command = ""
    for node in code_candidates:
        candidate = normalize_whitespace(node.get_text(" ", strip=True))
        if "npx skills add" in candidate:
            install_command = candidate
            break
    summary_text = _find_section_prose_text(soup, "Summary")
    skill_text = _find_section_prose_text(soup, "SKILL.md")
    description = _extract_first_paragraph(summary_text) or _extract_first_paragraph(skill_text)
    weekly_installs = parse_human_count(_find_labeled_metric_text(soup, "Weekly Installs"))
    return SkillPageRecord(
        skill_id=f"{owner}/{repo}/{skill_slug}",
        owner=owner,
        repo=repo,
        skill_slug=skill_slug,
        name=name,
        downloads=weekly_installs,
        repo_url=_extract_github_url(soup, owner=owner, repo=repo),
        install_command=install_command,
        description=description,
        summary_text=summary_text,
        skill_text=skill_text,
    )


def infer_permission_hints(*, name: str, description: str, summary_text: str, skill_text: str, install_command: str) -> tuple[dict[str, int], list[dict[str, object]]]:
    text = normalize_whitespace(" ".join([name, description, summary_text, skill_text, install_command])).lower()
    flags = {column: 0 for column in HINT_COLUMNS}
    evidence: list[dict[str, object]] = []

    def mark(flag: str, keywords: Iterable[str]) -> None:
        matches = sorted({keyword for keyword in keywords if keyword in text})
        if not matches:
            return
        flags[flag] = 1
        evidence.append({"flag": flag, "matches": matches[:12]})

    mark(
        "requires_api_key",
        [
            "api key",
            "apikey",
            "api_key",
            "anthropic_api_key",
            "openai_api_key",
            "secret key",
            "client secret",
        ],
    )
    mark(
        "requires_oauth",
        [
            "oauth",
            "oauth2",
            "authorize",
            "authorization code",
            "consent screen",
        ],
    )
    mark(
        "requires_token",
        [
            "access token",
            "bearer token",
            "personal access token",
            "token",
            "pat",
        ],
    )
    mark(
        "requires_local_file_access",
        [
            "file",
            "folder",
            "workspace",
            "directory",
            "markdown",
            "readme",
            "csv",
            "docx",
            "xlsx",
            "pptx",
            "save",
            "export",
        ],
    )
    mark(
        "requires_shell_access",
        [
            "bash",
            "shell",
            "terminal",
            "command line",
            "cli",
            "curl",
            "wget",
            "git ",
            "npm ",
            "npx ",
            "pnpm",
            "yarn",
            "pip ",
            "docker",
            "kubectl",
        ],
    )
    mark(
        "requires_browser_access",
        [
            "browser",
            "website",
            "web page",
            "playwright",
            "chrome",
            "tab",
            "url",
            "navigate",
        ],
    )
    mark("requires_github", ["github", "pull request", "pull-request", "repository", "repo", "issue"])
    mark("requires_slack", ["slack"])
    mark("requires_notion", ["notion"])
    mark("requires_calendar", ["calendar", "meeting", "schedule", "appointment", "event"])
    mark("requires_email", ["email", "gmail", "outlook", "mail"])
    mark("requires_docs", ["document", "docs", "docx", "spreadsheet", "slides", "pptx", "xlsx"])
    mark("requires_pdf", ["pdf"])
    return flags, evidence


class SkillsShArchiveDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS owners (
                owner TEXT PRIMARY KEY,
                source_count INTEGER DEFAULT 0,
                skill_count INTEGER DEFAULT 0,
                total_installs INTEGER DEFAULT 0,
                github_url TEXT DEFAULT '',
                page_url TEXT DEFAULT '',
                raw_html_path TEXT DEFAULT '',
                discovery_source TEXT DEFAULT '',
                fetched_at TEXT DEFAULT '',
                complete INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS repos (
                repo_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                repo TEXT NOT NULL,
                skill_count INTEGER DEFAULT 0,
                total_installs INTEGER DEFAULT 0,
                github_url TEXT DEFAULT '',
                install_command TEXT DEFAULT '',
                page_url TEXT DEFAULT '',
                raw_html_path TEXT DEFAULT '',
                discovery_source TEXT DEFAULT '',
                fetched_at TEXT DEFAULT '',
                complete INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS skills (
                skill_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                repo TEXT NOT NULL,
                skill_slug TEXT NOT NULL,
                author_handle TEXT DEFAULT '',
                name TEXT DEFAULT '',
                page_url TEXT DEFAULT '',
                repo_url TEXT DEFAULT '',
                description TEXT DEFAULT '',
                summary_text TEXT DEFAULT '',
                skill_text TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                metadata_systems TEXT DEFAULT '',
                install_command TEXT DEFAULT '',
                downloads INTEGER DEFAULT 0,
                stars INTEGER DEFAULT 0,
                total_installs INTEGER DEFAULT 0,
                scan_status TEXT DEFAULT '',
                raw_html_path TEXT DEFAULT '',
                discovered_from TEXT DEFAULT '',
                fetched_at TEXT DEFAULT '',
                detail_complete INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS permission_secret_hints (
                skill_id TEXT PRIMARY KEY,
                requires_api_key INTEGER DEFAULT 0,
                requires_oauth INTEGER DEFAULT 0,
                requires_token INTEGER DEFAULT 0,
                requires_local_file_access INTEGER DEFAULT 0,
                requires_shell_access INTEGER DEFAULT 0,
                requires_browser_access INTEGER DEFAULT 0,
                requires_github INTEGER DEFAULT 0,
                requires_slack INTEGER DEFAULT 0,
                requires_notion INTEGER DEFAULT 0,
                requires_calendar INTEGER DEFAULT 0,
                requires_email INTEGER DEFAULT 0,
                requires_docs INTEGER DEFAULT 0,
                requires_pdf INTEGER DEFAULT 0,
                raw_evidence TEXT DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS skill_versions (
                skill_id TEXT NOT NULL,
                version TEXT NOT NULL,
                PRIMARY KEY (skill_id, version)
            );

            CREATE TABLE IF NOT EXISTS search_discovery (
                query TEXT PRIMARY KEY,
                result_count INTEGER DEFAULT 0,
                fetched_at TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS crawl_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage TEXT NOT NULL,
                key TEXT NOT NULL,
                url TEXT NOT NULL,
                error_class TEXT NOT NULL,
                error_message TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            );
            """
        )
        self._ensure_column("skills", "repo_confirmed", "INTEGER DEFAULT 0")
        self._ensure_column("skills", "excluded_reason", "TEXT DEFAULT ''")
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_skills_pending_active
            ON skills(detail_complete, excluded_reason, downloads DESC, skill_id)
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_skills_repo_lookup
            ON skills(owner, repo, skill_slug)
            """
        )
        self.connection.commit()

    def _table_columns(self, table: str) -> set[str]:
        rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        if column in self._table_columns(table):
            return
        self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def log_failure(self, *, stage: str, key: str, url: str, error: Exception | str) -> None:
        error_class = error.__class__.__name__ if isinstance(error, Exception) else "Error"
        error_message = str(error)
        self.connection.execute(
            """
            INSERT INTO crawl_failures(stage, key, url, error_class, error_message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (stage, key, url, error_class, error_message, utcnow_iso()),
        )
        self.connection.commit()

    def upsert_owner_seed(self, owner: str, *, discovery_source: str) -> None:
        self.connection.execute(
            """
            INSERT INTO owners(owner, page_url, discovery_source)
            VALUES (?, ?, ?)
            ON CONFLICT(owner) DO UPDATE SET
                discovery_source = CASE
                    WHEN owners.discovery_source = '' THEN excluded.discovery_source
                    ELSE owners.discovery_source
                END
            """,
            (owner, f"https://skills.sh/{owner}", discovery_source),
        )
        self.connection.commit()

    def upsert_repo_seed(self, repo_id: str, *, discovery_source: str) -> None:
        owner, repo = repo_id.split("/", 1)
        self.connection.execute(
            """
            INSERT INTO repos(repo_id, owner, repo, page_url, discovery_source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repo_id) DO UPDATE SET
                discovery_source = CASE
                    WHEN repos.discovery_source = '' THEN excluded.discovery_source
                    ELSE repos.discovery_source
                END
            """,
            (repo_id, owner, repo, f"https://skills.sh/{repo_id}", discovery_source),
        )
        self.upsert_owner_seed(owner, discovery_source=discovery_source)
        self.connection.commit()

    def upsert_skill_seed(self, skill_id: str, *, discovery_source: str, downloads: int = 0) -> None:
        owner, repo, skill_slug = skill_id.split("/", 2)
        self.connection.execute(
            """
            INSERT INTO skills(
                skill_id, owner, repo, skill_slug, author_handle, page_url, discovered_from, downloads
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(skill_id) DO UPDATE SET
                downloads = MAX(skills.downloads, excluded.downloads),
                discovered_from = CASE
                    WHEN skills.discovered_from = '' THEN excluded.discovered_from
                    ELSE skills.discovered_from
                END
            """,
            (
                skill_id,
                owner,
                repo,
                skill_slug,
                owner,
                f"https://skills.sh/{skill_id}",
                discovery_source,
                downloads,
            ),
        )
        self.upsert_repo_seed(f"{owner}/{repo}", discovery_source=discovery_source)
        self.connection.commit()

    def record_search_query(self, query: str, result_count: int) -> None:
        self.connection.execute(
            """
            INSERT INTO search_discovery(query, result_count, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(query) DO UPDATE SET
                result_count = excluded.result_count,
                fetched_at = excluded.fetched_at
            """,
            (query, result_count, utcnow_iso()),
        )
        self.connection.commit()

    def processed_search_queries(self) -> set[str]:
        rows = self.connection.execute("SELECT query FROM search_discovery").fetchall()
        return {row["query"] for row in rows}

    def pending_owners(self, *, limit: int | None = None) -> list[str]:
        query = "SELECT owner FROM owners WHERE complete = 0 ORDER BY owner"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        return [row["owner"] for row in self.connection.execute(query).fetchall()]

    def pending_repos(self, *, limit: int | None = None) -> list[tuple[str, str]]:
        query = "SELECT owner, repo FROM repos WHERE complete = 0 ORDER BY repo_id"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        return [(row["owner"], row["repo"]) for row in self.connection.execute(query).fetchall()]

    def completed_repos(self, *, limit: int | None = None) -> list[tuple[str, str]]:
        query = "SELECT owner, repo FROM repos WHERE complete = 1 ORDER BY repo_id"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        return [(row["owner"], row["repo"]) for row in self.connection.execute(query).fetchall()]

    def pending_skill_details(self, *, limit: int | None = None) -> list[tuple[str, str, str]]:
        query = """
        SELECT owner, repo, skill_slug
        FROM skills
        WHERE detail_complete = 0
          AND excluded_reason = ''
        ORDER BY downloads DESC, skill_id
        """
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        return [
            (row["owner"], row["repo"], row["skill_slug"])
            for row in self.connection.execute(query).fetchall()
        ]

    def complete_owner(self, record: OwnerPageRecord, *, html_path: str = "") -> None:
        self.connection.execute(
            """
            INSERT INTO owners(
                owner, source_count, skill_count, total_installs, github_url, page_url, raw_html_path, fetched_at, complete
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(owner) DO UPDATE SET
                source_count = excluded.source_count,
                skill_count = excluded.skill_count,
                total_installs = excluded.total_installs,
                github_url = excluded.github_url,
                page_url = excluded.page_url,
                raw_html_path = excluded.raw_html_path,
                fetched_at = excluded.fetched_at,
                complete = 1
            """,
            (
                record.owner,
                record.source_count,
                record.skill_count,
                record.total_installs,
                record.github_url,
                f"https://skills.sh/{record.owner}",
                html_path,
                utcnow_iso(),
            ),
        )
        for repo_name, skill_count, total_installs in record.repos:
            repo_id = f"{record.owner}/{repo_name}"
            self.connection.execute(
                """
                INSERT INTO repos(repo_id, owner, repo, skill_count, total_installs, page_url, discovery_source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id) DO UPDATE SET
                    skill_count = MAX(repos.skill_count, excluded.skill_count),
                    total_installs = MAX(repos.total_installs, excluded.total_installs)
                """,
                (
                    repo_id,
                    record.owner,
                    repo_name,
                    skill_count,
                    total_installs,
                    f"https://skills.sh/{repo_id}",
                    "owner_page",
                ),
            )
        self.connection.commit()

    def reconcile_repo_catalog(self, owner: str, repo: str, confirmed_slugs: Iterable[str]) -> int:
        slugs = sorted({slug for slug in confirmed_slugs if slug})
        if slugs:
            placeholders = ", ".join("?" for _ in slugs)
            params = [owner, repo, *slugs]
            self.connection.execute(
                f"""
                UPDATE skills
                SET repo_confirmed = 1,
                    excluded_reason = ''
                WHERE owner = ?
                  AND repo = ?
                  AND skill_slug IN ({placeholders})
                """,
                params,
            )
            cursor = self.connection.execute(
                f"""
                UPDATE skills
                SET excluded_reason = 'repo_page_missing'
                WHERE owner = ?
                  AND repo = ?
                  AND detail_complete = 0
                  AND repo_confirmed = 0
                  AND skill_slug NOT IN ({placeholders})
                """,
                params,
            )
        else:
            cursor = self.connection.execute(
                """
                UPDATE skills
                SET excluded_reason = 'repo_page_missing'
                WHERE owner = ?
                  AND repo = ?
                  AND detail_complete = 0
                  AND repo_confirmed = 0
                """,
                (owner, repo),
            )
        self.connection.commit()
        return int(cursor.rowcount or 0)

    def complete_repo(self, record: RepoPageRecord, *, html_path: str = "") -> None:
        repo_id = f"{record.owner}/{record.repo}"
        self.connection.execute(
            """
            INSERT INTO repos(
                repo_id, owner, repo, skill_count, total_installs, github_url, install_command,
                page_url, raw_html_path, fetched_at, complete
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(repo_id) DO UPDATE SET
                skill_count = excluded.skill_count,
                total_installs = excluded.total_installs,
                github_url = excluded.github_url,
                install_command = excluded.install_command,
                page_url = excluded.page_url,
                raw_html_path = excluded.raw_html_path,
                fetched_at = excluded.fetched_at,
                complete = 1
            """,
            (
                repo_id,
                record.owner,
                record.repo,
                record.skill_count,
                record.total_installs,
                record.github_url,
                record.install_command,
                f"https://skills.sh/{repo_id}",
                html_path,
                utcnow_iso(),
            ),
        )
        confirmed_slugs = [skill_slug for skill_slug, _, _ in record.skills]
        for skill_slug, name, installs in record.skills:
            skill_id = f"{record.owner}/{record.repo}/{skill_slug}"
            self.connection.execute(
                """
                INSERT INTO skills(
                    skill_id, owner, repo, skill_slug, author_handle, name, page_url,
                    repo_url, install_command, downloads, total_installs, discovered_from,
                    repo_confirmed, excluded_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '')
                ON CONFLICT(skill_id) DO UPDATE SET
                    name = CASE WHEN skills.name = '' THEN excluded.name ELSE skills.name END,
                    repo_url = CASE WHEN skills.repo_url = '' THEN excluded.repo_url ELSE skills.repo_url END,
                    install_command = CASE
                        WHEN skills.install_command = '' THEN excluded.install_command
                        ELSE skills.install_command
                    END,
                    downloads = MAX(skills.downloads, excluded.downloads),
                    total_installs = MAX(skills.total_installs, excluded.total_installs),
                    repo_confirmed = 1,
                    excluded_reason = ''
                """,
                (
                    skill_id,
                    record.owner,
                    record.repo,
                    skill_slug,
                    record.owner,
                    name,
                    f"https://skills.sh/{skill_id}",
                    record.github_url,
                    record.install_command,
                    installs,
                    record.total_installs,
                    "repo_page",
                ),
            )
        self.reconcile_repo_catalog(record.owner, record.repo, confirmed_slugs)
        self.connection.commit()

    def complete_skill(
        self,
        record: SkillPageRecord,
        *,
        hints: dict[str, int],
        raw_evidence: list[dict[str, object]],
        html_path: str = "",
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO skills(
                skill_id, owner, repo, skill_slug, author_handle, name, page_url, repo_url,
                description, summary_text, skill_text, install_command, downloads, raw_html_path,
                fetched_at, detail_complete, excluded_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '')
            ON CONFLICT(skill_id) DO UPDATE SET
                author_handle = excluded.author_handle,
                name = excluded.name,
                page_url = excluded.page_url,
                repo_url = excluded.repo_url,
                description = excluded.description,
                summary_text = excluded.summary_text,
                skill_text = excluded.skill_text,
                install_command = excluded.install_command,
                downloads = MAX(skills.downloads, excluded.downloads),
                raw_html_path = excluded.raw_html_path,
                fetched_at = excluded.fetched_at,
                detail_complete = 1,
                excluded_reason = ''
            """,
            (
                record.skill_id,
                record.owner,
                record.repo,
                record.skill_slug,
                record.owner,
                record.name,
                f"https://skills.sh/{record.skill_id}",
                record.repo_url,
                record.description,
                record.summary_text,
                record.skill_text,
                record.install_command,
                record.downloads,
                html_path,
                utcnow_iso(),
            ),
        )
        hint_values = [hints[column] for column in HINT_COLUMNS]
        self.connection.execute(
            f"""
            INSERT INTO permission_secret_hints(skill_id, {", ".join(HINT_COLUMNS)}, raw_evidence)
            VALUES (?, {", ".join("?" for _ in HINT_COLUMNS)}, ?)
            ON CONFLICT(skill_id) DO UPDATE SET
                {", ".join(f"{column} = excluded.{column}" for column in HINT_COLUMNS)},
                raw_evidence = excluded.raw_evidence
            """,
            [record.skill_id, *hint_values, json.dumps(raw_evidence, ensure_ascii=False)],
        )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO skill_versions(skill_id, version)
            VALUES (?, ?)
            """,
            (record.skill_id, "skills.sh"),
        )
        self.connection.commit()

    def summary(self) -> dict[str, int]:
        tables = {
            "owners": "SELECT COUNT(*) FROM owners",
            "repos": "SELECT COUNT(*) FROM repos",
            "skills": "SELECT COUNT(*) FROM skills WHERE excluded_reason = ''",
            "skills_raw": "SELECT COUNT(*) FROM skills",
            "skills_excluded": "SELECT COUNT(*) FROM skills WHERE excluded_reason != ''",
            "skills_repo_confirmed": "SELECT COUNT(*) FROM skills WHERE repo_confirmed = 1",
            "skill_details": "SELECT COUNT(*) FROM skills WHERE detail_complete = 1",
            "owner_pages": "SELECT COUNT(*) FROM owners WHERE complete = 1",
            "repo_pages": "SELECT COUNT(*) FROM repos WHERE complete = 1",
            "failures": "SELECT COUNT(*) FROM crawl_failures",
        }
        return {
            key: int(self.connection.execute(query).fetchone()[0])
            for key, query in tables.items()
        }


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def save_html_if_needed(*, output_root: Path, relative_path: Path, html: str, enabled: bool) -> str:
    if not enabled:
        return ""
    path = output_root / relative_path
    write_text(path, html)
    return str(path)


def export_skill_artifact(output_root: Path, record: SkillPageRecord, *, html: str, save_html: bool) -> str:
    archive_dir = (
        output_root
        / "archive"
        / slug_path_component(record.owner)
        / slug_path_component(record.repo)
        / slug_path_component(record.skill_slug)
    )
    write_text(archive_dir / "SKILL.md", record.skill_text or record.summary_text or record.description)
    if record.summary_text:
        write_text(archive_dir / "SUMMARY.txt", record.summary_text)
    write_json(
        archive_dir / "metadata.json",
        {
            "skill_id": record.skill_id,
            "owner": record.owner,
            "repo": record.repo,
            "skill_slug": record.skill_slug,
            "name": record.name,
            "page_url": f"https://skills.sh/{record.skill_id}",
            "repo_url": record.repo_url,
            "install_command": record.install_command,
            "weekly_installs": record.downloads,
            "description": record.description,
        },
    )
    if save_html:
        write_text(archive_dir / "page.html", html)
        return str(archive_dir / "page.html")
    return ""


def seed_from_sitemap(db: SkillsShArchiveDB, *, output_root: Path, save_html: bool = True) -> int:
    html = fetch_url("https://skills.sh/sitemap.xml")
    relative = Path("discovery") / "sitemap.xml"
    save_html_if_needed(output_root=output_root, relative_path=relative, html=html, enabled=save_html)
    paths = re.findall(r"<loc>https://skills\.sh/([^<]+)</loc>", html)
    for path in paths:
        db.upsert_skill_seed(path, discovery_source="sitemap")
    return len(paths)


def discover_from_search_bigrams(
    db: SkillsShArchiveDB,
    *,
    query_limit: int = SEARCH_RESULT_LIMIT,
    max_workers: int = 12,
) -> dict[str, int]:
    processed = db.processed_search_queries()
    queries = [
        "".join(pair)
        for pair in itertools.product(SEARCH_ALPHABET, repeat=2)
        if "".join(pair) not in processed
    ]
    stats = {"queries": 0, "results": 0, "new_repos": 0}
    if not queries:
        log_progress("search_discovery: no pending queries")
        return stats
    log_progress(
        f"search_discovery: starting pending_queries={len(queries)} workers={max_workers} limit={query_limit}"
    )

    def fetch(query: str) -> tuple[str, dict[str, object] | None, str | None]:
        url = f"https://skills.sh/api/search?q={urllib_parse.quote(query)}&limit={query_limit}"
        try:
            return query, json.loads(fetch_url(url)), None
        except urllib_error.HTTPError as exc:
            return query, None, f"HTTP{exc.code}"
        except Exception as exc:  # pragma: no cover - network failures depend on runtime
            return query, None, exc.__class__.__name__

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for index, (query, payload, error) in enumerate(executor.map(fetch, queries), start=1):
            stats["queries"] += 1
            if error:
                db.log_failure(
                    stage="search_discovery",
                    key=query,
                    url=f"https://skills.sh/api/search?q={urllib_parse.quote(query)}&limit={query_limit}",
                    error=error,
                )
                db.record_search_query(query, 0)
                continue
            skills = payload.get("skills", []) if isinstance(payload, dict) else []
            db.record_search_query(query, len(skills))
            stats["results"] += len(skills)
            for skill in skills:
                skill_id = skill.get("id")
                if not isinstance(skill_id, str) or skill_id.count("/") != 2:
                    continue
                owner, repo, _skill_slug = skill_id.split("/", 2)
                db.upsert_repo_seed(
                    f"{owner}/{repo}",
                    discovery_source=f"search:{query}",
                )
                stats["new_repos"] += 1
            if index == 1 or index % 100 == 0 or index == len(queries):
                log_progress(
                    "search_discovery: "
                    f"processed={index}/{len(queries)} "
                    f"results={stats['results']} repo_seeds={stats['new_repos']}"
                )
    return stats


def crawl_owner_pages(
    db: SkillsShArchiveDB,
    *,
    output_root: Path,
    save_html: bool,
    limit: int | None = None,
    max_workers: int = DEFAULT_PAGE_WORKERS,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
) -> int:
    owners = db.pending_owners(limit=limit)
    if not owners:
        log_progress("owner_pages: no pending owners")
        return 0

    log_progress(
        f"owner_pages: starting pending={len(owners)} workers={max_workers} limit={limit or 'all'}"
    )

    def fetch(owner: str) -> tuple[str, str, str | None, OwnerPageRecord | None, Exception | None]:
        url = f"https://skills.sh/{owner}"
        try:
            html = fetch_url(url)
            record = parse_owner_page(html, owner)
            return owner, url, html, record, None
        except Exception as exc:
            return owner, url, None, None, exc

    completed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for index, (owner, url, html, record, error) in enumerate(executor.map(fetch, owners), start=1):
            try:
                if error is not None or html is None or record is None:
                    failed += 1
                    db.log_failure(stage="owner_page", key=owner, url=url, error=error or "unknown owner error")
                else:
                    html_path = save_html_if_needed(
                        output_root=output_root,
                        relative_path=Path("pages") / "owners" / f"{slug_path_component(owner)}.html",
                        html=html,
                        enabled=save_html,
                    )
                    db.complete_owner(record, html_path=html_path)
                    completed += 1
            except Exception as exc:
                failed += 1
                db.log_failure(stage="owner_page", key=owner, url=url, error=exc)
            if index == 1 or index % progress_every == 0 or index == len(owners):
                log_progress(
                    f"owner_pages: processed={index}/{len(owners)} completed={completed} failed={failed}"
                )
    return completed


def crawl_repo_pages(
    db: SkillsShArchiveDB,
    *,
    output_root: Path,
    save_html: bool,
    limit: int | None = None,
    max_workers: int = DEFAULT_PAGE_WORKERS,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
) -> int:
    repos = db.pending_repos(limit=limit)
    if not repos:
        log_progress("repo_pages: no pending repos")
        return 0

    log_progress(
        f"repo_pages: starting pending={len(repos)} workers={max_workers} limit={limit or 'all'}"
    )

    def fetch(repo_key: tuple[str, str]) -> tuple[str, str, str, str | None, RepoPageRecord | None, Exception | None]:
        owner, repo = repo_key
        repo_id = f"{owner}/{repo}"
        url = f"https://skills.sh/{repo_id}"
        try:
            html = fetch_url(url)
            record = parse_repo_page(html, owner, repo)
            return owner, repo, url, html, record, None
        except Exception as exc:
            return owner, repo, url, None, None, exc

    completed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for index, (owner, repo, url, html, record, error) in enumerate(executor.map(fetch, repos), start=1):
            repo_id = f"{owner}/{repo}"
            try:
                if error is not None or html is None or record is None:
                    failed += 1
                    db.log_failure(stage="repo_page", key=repo_id, url=url, error=error or "unknown repo error")
                else:
                    html_path = save_html_if_needed(
                        output_root=output_root,
                        relative_path=Path("pages") / "repos" / slug_path_component(owner) / f"{slug_path_component(repo)}.html",
                        html=html,
                        enabled=save_html,
                    )
                    db.complete_repo(record, html_path=html_path)
                    completed += 1
            except Exception as exc:
                failed += 1
                db.log_failure(stage="repo_page", key=repo_id, url=url, error=exc)
            if index == 1 or index % progress_every == 0 or index == len(repos):
                log_progress(
                    f"repo_pages: processed={index}/{len(repos)} completed={completed} failed={failed}"
                )
    return completed


def crawl_skill_pages(
    db: SkillsShArchiveDB,
    *,
    output_root: Path,
    save_html: bool,
    limit: int | None = None,
    max_workers: int = DEFAULT_PAGE_WORKERS,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
) -> int:
    pending = db.pending_skill_details(limit=limit)
    if not pending:
        log_progress("skill_pages: no pending skills")
        return 0

    log_progress(
        f"skill_pages: starting pending={len(pending)} workers={max_workers} limit={limit or 'all'}"
    )

    def fetch(
        skill_key: tuple[str, str, str]
    ) -> tuple[str, str, str, str, str | None, SkillPageRecord | None, Exception | None]:
        owner, repo, skill_slug = skill_key
        skill_id = f"{owner}/{repo}/{skill_slug}"
        url = f"https://skills.sh/{skill_id}"
        try:
            html = fetch_url(url)
            record = parse_skill_page(html, owner, repo, skill_slug)
            return owner, repo, skill_slug, url, html, record, None
        except Exception as exc:
            return owner, repo, skill_slug, url, None, None, exc

    completed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for index, (owner, repo, skill_slug, url, html, record, error) in enumerate(
            executor.map(fetch, pending), start=1
        ):
            skill_id = f"{owner}/{repo}/{skill_slug}"
            try:
                if error is not None or html is None or record is None:
                    failed += 1
                    db.log_failure(stage="skill_page", key=skill_id, url=url, error=error or "unknown skill error")
                else:
                    flags, evidence = infer_permission_hints(
                        name=record.name,
                        description=record.description,
                        summary_text=record.summary_text,
                        skill_text=record.skill_text,
                        install_command=record.install_command,
                    )
                    html_path = export_skill_artifact(output_root, record, html=html, save_html=save_html)
                    db.complete_skill(record, hints=flags, raw_evidence=evidence, html_path=html_path)
                    completed += 1
            except Exception as exc:
                failed += 1
                db.log_failure(stage="skill_page", key=skill_id, url=url, error=exc)
            if index == 1 or index % progress_every == 0 or index == len(pending):
                log_progress(
                    f"skill_pages: processed={index}/{len(pending)} completed={completed} failed={failed}"
                )
    return completed


def reconcile_completed_repo_catalog(
    db: SkillsShArchiveDB,
    *,
    limit: int | None = None,
    max_workers: int = DEFAULT_PAGE_WORKERS,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
) -> int:
    repos = db.completed_repos(limit=limit)
    if not repos:
        log_progress("repo_reconcile: no completed repos")
        return 0

    log_progress(
        f"repo_reconcile: starting repos={len(repos)} workers={max_workers} limit={limit or 'all'}"
    )

    def fetch(repo_key: tuple[str, str]) -> tuple[str, str, str, RepoPageRecord | None, Exception | None]:
        owner, repo = repo_key
        url = f"https://skills.sh/{owner}/{repo}"
        try:
            html = fetch_url(url)
            record = parse_repo_page(html, owner, repo)
            return owner, repo, url, record, None
        except Exception as exc:
            return owner, repo, url, None, exc

    completed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for index, (owner, repo, url, record, error) in enumerate(executor.map(fetch, repos), start=1):
            try:
                if error is not None or record is None:
                    failed += 1
                    db.log_failure(stage="repo_reconcile", key=f"{owner}/{repo}", url=url, error=error or "unknown repo reconcile error")
                else:
                    db.complete_repo(record, html_path="")
                    completed += 1
            except Exception as exc:
                failed += 1
                db.log_failure(stage="repo_reconcile", key=f"{owner}/{repo}", url=url, error=exc)
            if index == 1 or index % progress_every == 0 or index == len(repos):
                log_progress(
                    f"repo_reconcile: processed={index}/{len(repos)} completed={completed} failed={failed}"
                )
    return completed


def run_skills_sh_archive(
    *,
    output_root: Path,
    db_path: Path | None = None,
    save_html: bool = False,
    discover_search_bigrams: bool = False,
    owner_limit: int | None = None,
    repo_limit: int | None = None,
    skill_limit: int | None = None,
) -> dict[str, int]:
    output_root.mkdir(parents=True, exist_ok=True)
    db = SkillsShArchiveDB(db_path or (output_root / "skills_sh.sqlite3"))
    try:
        log_progress(f"archive_root={output_root}")
        log_progress(
            "resume_summary: "
            + " ".join(f"{key}={value}" for key, value in db.summary().items())
        )
        sitemap_skills = seed_from_sitemap(db, output_root=output_root, save_html=save_html)
        search_stats = {"queries": 0, "results": 0, "new_repos": 0}
        if discover_search_bigrams:
            search_stats = discover_from_search_bigrams(db)
        owner_pages = crawl_owner_pages(db, output_root=output_root, save_html=save_html, limit=owner_limit)
        repo_pages = crawl_repo_pages(db, output_root=output_root, save_html=save_html, limit=repo_limit)
        skill_pages = crawl_skill_pages(db, output_root=output_root, save_html=save_html, limit=skill_limit)
        summary = db.summary()
        log_progress("final_summary: " + " ".join(f"{key}={value}" for key, value in summary.items()))
        return {
            "sitemap_skill_seeds": sitemap_skills,
            "search_queries_processed": search_stats["queries"],
            "search_results_seen": search_stats["results"],
            "search_repo_seeds": search_stats["new_repos"],
            "owner_pages_completed": owner_pages,
            "repo_pages_completed": repo_pages,
            "skill_pages_completed": skill_pages,
            **summary,
        }
    finally:
        db.close()
