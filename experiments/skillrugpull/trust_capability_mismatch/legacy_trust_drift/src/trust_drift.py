from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterable
import re
import sqlite3


AGENT_FACING_EXACT = {
    "skill.md",
    "readme.md",
    "skill.json",
    "skill.yaml",
    "clawhub.json",
    "_meta.json",
    "security.md",
}

AGENT_FACING_PREFIXES = (
    "agents/",
)

CODE_SUFFIXES = (
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".sh",
    ".bash",
    ".zsh",
    ".go",
    ".rb",
    ".rs",
    ".java",
    ".kt",
    ".swift",
)

PERMISSION_TERMS = {
    "api key",
    "oauth",
    "token",
    "auth",
    "secret",
    "credential",
    "github",
    "slack",
    "notion",
    "calendar",
    "email",
    "gmail",
    "docs",
    "pdf",
    "browser",
    "shell",
    "local file",
    "workspace",
    "webhook",
}

ACTION_TERMS = {
    "send",
    "post",
    "publish",
    "execute",
    "run",
    "shell",
    "write",
    "delete",
    "modify",
    "control",
    "automate",
    "sync",
    "upload",
    "backup",
    "deploy",
}

TRUST_TERMS = {
    "safe",
    "secure",
    "trusted",
    "official",
    "verified",
    "production-ready",
    "battle-tested",
    "recommended",
    "reliable",
    "audited",
}

COVERT_TERMS = {
    "memory",
    "history",
    "journal",
    "workspace",
    "browser",
    "localstorage",
    "cookie",
    "background",
    "proactive",
    "cache",
    "notes",
    "device",
}

GENERIC_CHANGELOG_TERMS = {
    "bug fix",
    "bugfix",
    "fixes",
    "minor improvements",
    "improvements",
    "maintenance",
    "update",
    "refactor",
    "cleanup",
    "polish",
}

SCAN_RANK = {
    None: 0,
    "benign": 1,
    "pending": 2,
    "suspicious": 3,
    "error": 4,
    "malicious": 5,
}


@dataclass(frozen=True)
class VersionRecord:
    skill_id: str
    version: str
    release_time: str | None
    changelog: str | None
    bundle_hash: str | None
    manifest_hash: str | None
    dependency_hash: str | None
    scan_status: str | None
    security_has_warnings: bool
    files_count: int


@dataclass(frozen=True)
class DriftTransition:
    skill_id: str
    previous_version: str
    current_version: str
    agent_facing_changed_files: int
    code_changed_files: int
    permission_term_increase: int
    action_term_increase: int
    trust_term_increase: int
    covert_term_increase: int
    scan_worsened: bool
    generic_or_empty_changelog: bool
    semantic_drift_candidate: bool
    rug_pull_candidate: bool


@dataclass(frozen=True)
class VersionView:
    agent_files: dict[str, str | None]
    code_files: dict[str, str | None]


def _parse_datetime(value: str | None) -> tuple[int, datetime | None]:
    if not value:
        return (1, None)
    try:
        return (0, datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return (0, None)


def _natural_version_key(version: str) -> tuple[object, ...]:
    parts = re.split(r"([0-9]+)", version or "")
    key: list[object] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.lower())
    return tuple(key)


def version_sort_key(record: VersionRecord) -> tuple[object, ...]:
    time_priority, parsed_time = _parse_datetime(record.release_time)
    if parsed_time is not None:
        return (time_priority, parsed_time, _natural_version_key(record.version))
    return (time_priority, _natural_version_key(record.version))


def is_agent_facing_path(path: str) -> bool:
    normalized = path.lower()
    if normalized in AGENT_FACING_EXACT:
        return True
    if any(normalized.startswith(prefix) for prefix in AGENT_FACING_PREFIXES):
        return True
    if "prompt" in normalized and normalized.endswith((".md", ".json", ".yaml", ".yml")):
        return True
    return False


def is_code_path(path: str) -> bool:
    normalized = path.lower()
    if is_agent_facing_path(normalized):
        return False
    return normalized.endswith(CODE_SUFFIXES)


def _resolve_text_path(project_root: Path, text_path: str | None) -> Path | None:
    if not text_path:
        return None
    candidate = Path(text_path)
    if candidate.is_absolute():
        absolute_text = str(candidate)
        needle = "/clawhubS/"
        if needle in absolute_text:
            suffix = absolute_text.split(needle, 1)[1]
            return project_root / suffix
        return candidate
    return project_root / candidate


@lru_cache(maxsize=50000)
def _read_text_cached(resolved_path: str) -> str:
    path = Path(resolved_path)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _read_text(project_root: Path, text_path: str | None) -> str:
    resolved = _resolve_text_path(project_root, text_path)
    if not resolved:
        return ""
    return _read_text_cached(str(resolved))


def _term_hits(text: str, terms: Iterable[str]) -> set[str]:
    lower = text.lower()
    return {term for term in terms if term in lower}


def _generic_or_empty_changelog(changelog: str | None) -> bool:
    if not changelog or not changelog.strip():
        return True
    lower = changelog.lower()
    return any(term in lower for term in GENERIC_CHANGELOG_TERMS)


def _scan_worsened(previous: VersionRecord, current: VersionRecord) -> bool:
    previous_rank = SCAN_RANK.get((previous.scan_status or "").lower() or None, 0)
    current_rank = SCAN_RANK.get((current.scan_status or "").lower() or None, 0)
    if current_rank > previous_rank:
        return True
    return (not previous.security_has_warnings) and current.security_has_warnings


def load_versions(connection: sqlite3.Connection) -> dict[str, list[VersionRecord]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT skill_id, version, release_time, changelog, bundle_hash, manifest_hash,
               dependency_hash, scan_status, COALESCE(security_has_warnings, 0) AS security_has_warnings,
               COALESCE(files_count, 0) AS files_count
        FROM skill_versions
        """
    ).fetchall()
    grouped: dict[str, list[VersionRecord]] = {}
    for row in rows:
        grouped.setdefault(row["skill_id"], []).append(
            VersionRecord(
                skill_id=row["skill_id"],
                version=row["version"],
                release_time=row["release_time"],
                changelog=row["changelog"],
                bundle_hash=row["bundle_hash"],
                manifest_hash=row["manifest_hash"],
                dependency_hash=row["dependency_hash"],
                scan_status=row["scan_status"],
                security_has_warnings=bool(row["security_has_warnings"]),
                files_count=int(row["files_count"] or 0),
            )
        )
    for skill_id in grouped:
        grouped[skill_id].sort(key=version_sort_key)
    return grouped


def load_version_files(connection: sqlite3.Connection) -> dict[tuple[str, str], dict[str, tuple[str | None, str | None]]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT skill_id, version, path, sha256, text_path
        FROM skill_files
        """
    ).fetchall()
    result: dict[tuple[str, str], dict[str, tuple[str | None, str | None]]] = {}
    for row in rows:
        result.setdefault((row["skill_id"], row["version"]), {})[row["path"]] = (
            row["sha256"],
            row["text_path"],
        )
    return result


def analyze_trust_drift(db_path: Path, project_root: Path) -> tuple[dict[str, int], list[DriftTransition]]:
    connection = sqlite3.connect(db_path)
    versions_by_skill = load_versions(connection)
    files_by_version = load_version_files(connection)
    connection.close()

    version_views: dict[tuple[str, str], VersionView] = {}
    for version_key, file_map in files_by_version.items():
        agent_files: dict[str, str | None] = {}
        code_files: dict[str, str | None] = {}
        for path, (sha256, text_path) in file_map.items():
            if is_agent_facing_path(path):
                agent_files[path] = sha256
            elif is_code_path(path):
                code_files[path] = sha256
        version_views[version_key] = VersionView(
            agent_files=agent_files,
            code_files=code_files,
        )

    transitions: list[DriftTransition] = []
    summary = {
        "skills_with_multiple_versions": 0,
        "version_transitions": 0,
        "transitions_with_agent_facing_change": 0,
        "transitions_with_permission_expansion": 0,
        "transitions_with_action_expansion": 0,
        "transitions_with_trust_expansion": 0,
        "transitions_with_covert_expansion": 0,
        "transitions_with_scan_worsening": 0,
        "semantic_drift_candidates": 0,
        "rug_pull_candidates": 0,
    }

    for skill_id, versions in versions_by_skill.items():
        if len(versions) < 2:
            continue
        summary["skills_with_multiple_versions"] += 1
        for previous, current in zip(versions, versions[1:]):
            summary["version_transitions"] += 1
            prev_view = version_views.get((skill_id, previous.version), VersionView({}, {}))
            curr_view = version_views.get((skill_id, current.version), VersionView({}, {}))

            changed_agent_paths = [
                path
                for path in set(prev_view.agent_files) | set(curr_view.agent_files)
                if prev_view.agent_files.get(path) != curr_view.agent_files.get(path)
            ]
            agent_changed_files = sum(
                1 for _ in changed_agent_paths
            )
            code_changed_files = sum(
                prev_view.code_files.get(path) != curr_view.code_files.get(path)
                for path in set(prev_view.code_files) | set(curr_view.code_files)
            )

            prev_file_map = files_by_version.get((skill_id, previous.version), {})
            curr_file_map = files_by_version.get((skill_id, current.version), {})
            prev_text_blob = "\n".join(
                _read_text(project_root, prev_file_map.get(path, (None, None))[1])
                for path in changed_agent_paths
            )
            curr_text_blob = "\n".join(
                _read_text(project_root, curr_file_map.get(path, (None, None))[1])
                for path in changed_agent_paths
            )

            permission_increase = max(
                0,
                len(_term_hits(curr_text_blob, PERMISSION_TERMS)) - len(_term_hits(prev_text_blob, PERMISSION_TERMS)),
            )
            action_increase = max(
                0,
                len(_term_hits(curr_text_blob, ACTION_TERMS)) - len(_term_hits(prev_text_blob, ACTION_TERMS)),
            )
            trust_increase = max(
                0,
                len(_term_hits(curr_text_blob, TRUST_TERMS)) - len(_term_hits(prev_text_blob, TRUST_TERMS)),
            )
            covert_increase = max(
                0,
                len(_term_hits(curr_text_blob, COVERT_TERMS)) - len(_term_hits(prev_text_blob, COVERT_TERMS)),
            )

            scan_worsened = _scan_worsened(previous, current)
            generic_or_empty = _generic_or_empty_changelog(current.changelog)

            semantic_candidate = agent_changed_files > 0 and (
                permission_increase > 0
                or action_increase > 0
                or trust_increase > 0
                or covert_increase > 0
                or scan_worsened
            )

            rug_pull_candidate = semantic_candidate and (
                code_changed_files <= 2
                or generic_or_empty
            )

            if agent_changed_files > 0:
                summary["transitions_with_agent_facing_change"] += 1
            if permission_increase > 0:
                summary["transitions_with_permission_expansion"] += 1
            if action_increase > 0:
                summary["transitions_with_action_expansion"] += 1
            if trust_increase > 0:
                summary["transitions_with_trust_expansion"] += 1
            if covert_increase > 0:
                summary["transitions_with_covert_expansion"] += 1
            if scan_worsened:
                summary["transitions_with_scan_worsening"] += 1
            if semantic_candidate:
                summary["semantic_drift_candidates"] += 1
            if rug_pull_candidate:
                summary["rug_pull_candidates"] += 1

            transitions.append(
                DriftTransition(
                    skill_id=skill_id,
                    previous_version=previous.version,
                    current_version=current.version,
                    agent_facing_changed_files=agent_changed_files,
                    code_changed_files=code_changed_files,
                    permission_term_increase=permission_increase,
                    action_term_increase=action_increase,
                    trust_term_increase=trust_increase,
                    covert_term_increase=covert_increase,
                    scan_worsened=scan_worsened,
                    generic_or_empty_changelog=generic_or_empty,
                    semantic_drift_candidate=semantic_candidate,
                    rug_pull_candidate=rug_pull_candidate,
                )
            )

    transitions.sort(
        key=lambda item: (
            item.rug_pull_candidate,
            item.semantic_drift_candidate,
            item.permission_term_increase + item.action_term_increase + item.trust_term_increase + item.covert_term_increase,
            item.agent_facing_changed_files,
            -item.code_changed_files,
        ),
        reverse=True,
    )
    return summary, transitions
