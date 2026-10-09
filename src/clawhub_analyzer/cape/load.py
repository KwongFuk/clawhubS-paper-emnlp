"""
CAPE — Capability Extraction from All files Pipeline
load.py: Load skill files from the database and build SkillProfiles.

Handles both:
  - ClawHub DB (skill_files.text_path → local disk files)
  - skills.sh DB (skills.skill_text field directly)
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Iterator

from clawhub_analyzer.cape.extract import extract_from_file
from clawhub_analyzer.cape.types import Capability, SkillProfile

LOGGER = logging.getLogger(__name__)

# Channel that is structurally always present between any two co-deployed skills.
_ALWAYS_PRESENT_CHANNEL = Capability(
    cap_type="channel",
    label="same_turn_ctx",
    evidence="structural:always_present",
)


# ─── ClawHub DB loader ────────────────────────────────────────────────────────

def load_clawhub_profile(
    skill_id: str,
    rows: list[sqlite3.Row],
    skill_meta: sqlite3.Row,
) -> SkillProfile:
    """
    Build a SkillProfile for one ClawHub skill by reading all its text files.

    rows      : rows from skill_files WHERE skill_id = ?
    skill_meta: one row from skills table
    """
    all_caps: list[Capability] = []

    for row in rows:
        text_path: str | None = row["text_path"]
        file_path: str = row["path"] or ""
        if not text_path:
            continue
        if not os.path.exists(text_path):
            LOGGER.debug("Missing file on disk: %s", text_path)
            continue
        try:
            text = Path(text_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            LOGGER.debug("Cannot read %s: %s", text_path, exc)
            continue

        caps = extract_from_file(text, file_path)
        all_caps.extend(caps)

    # Always add the structural channel
    all_caps.append(_ALWAYS_PRESENT_CHANNEL)

    # Deduplicate by (cap_type, label, role) — keep read/write channel roles distinct.
    seen: set[tuple[str, str, str | None]] = set()
    deduped: list[Capability] = []
    for c in all_caps:
        key = (c.cap_type, c.label, c.role)
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    return SkillProfile(
        skill_id=skill_id,
        name=skill_meta["name"] or skill_id,
        downloads=(skill_meta["downloads"] or 0) + (skill_meta["installs_all_time"] or 0),
        author=skill_meta["author_handle"],
        capabilities=deduped,
    )


def stream_clawhub_profiles(
    db_path: str,
    *,
    min_downloads: int = 0,
    limit: int | None = None,
) -> Iterator[SkillProfile]:
    """
    Stream SkillProfiles from a ClawHub SQLite database.
    Reads all skill_files per skill, extracts capabilities, yields SkillProfile.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    # Load all skill metadata
    skill_rows = con.execute(
        """
        SELECT skill_id, name, author_handle, downloads, installs_all_time
        FROM skills
        ORDER BY (COALESCE(downloads, 0) + COALESCE(installs_all_time, 0)) DESC
        """
    ).fetchall()

    # Build index: skill_id → list[file_row]
    LOGGER.info("Indexing skill_files …")
    file_rows_all = con.execute(
        """
        SELECT skill_id, path, text_path
        FROM skill_files
        WHERE text_path IS NOT NULL AND text_path != ''
        """
    ).fetchall()
    file_index: dict[str, list[sqlite3.Row]] = {}
    for row in file_rows_all:
        file_index.setdefault(row["skill_id"], []).append(row)

    count = 0
    for skill_meta in skill_rows:
        sid = skill_meta["skill_id"]
        dl = (skill_meta["downloads"] or 0) + (skill_meta["installs_all_time"] or 0)
        if dl < min_downloads:
            continue

        file_rows = file_index.get(sid, [])
        profile = load_clawhub_profile(sid, file_rows, skill_meta)
        yield profile

        count += 1
        if limit and count >= limit:
            break

    con.close()


# ─── skills.sh DB loader ──────────────────────────────────────────────────────

def stream_skillssh_profiles(
    db_path: str,
    *,
    min_downloads: int = 0,
    limit: int | None = None,
) -> Iterator[SkillProfile]:
    """
    Stream SkillProfiles from a skills.sh SQLite database.
    Uses the skill_text field (= full SKILL.md content) directly.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        """
        SELECT skill_id, name, author_handle, total_installs, skill_text, summary_text
        FROM skills
        WHERE detail_complete = 1
        ORDER BY COALESCE(total_installs, 0) DESC
        """
    ).fetchall()

    count = 0
    for row in rows:
        dl = row["total_installs"] or 0
        if dl < min_downloads:
            continue

        sid: str = row["skill_id"]
        text: str = row["skill_text"] or row["summary_text"] or ""

        # skills.sh skills are Markdown-based; treat as SKILL.md
        caps = extract_from_file(text, "SKILL.md")
        caps.append(_ALWAYS_PRESENT_CHANNEL)

        # Deduplicate
        seen: set[tuple[str, str, str | None]] = set()
        deduped: list[Capability] = []
        for c in caps:
            key = (c.cap_type, c.label, c.role)
            if key not in seen:
                seen.add(key)
                deduped.append(c)

        yield SkillProfile(
            skill_id=sid,
            name=row["name"] or sid,
            downloads=dl,
            author=row["author_handle"],
            capabilities=deduped,
        )

        count += 1
        if limit and count >= limit:
            break

    con.close()
