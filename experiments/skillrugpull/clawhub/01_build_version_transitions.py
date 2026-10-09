#!/usr/bin/env python3
"""
clawhub/01_build_version_transitions.py
----------------------------------------
Build version transition pairs from ClawHub's real version history.

For each skill with 2+ versions:
  - Sort versions by release_time
  - Create consecutive (v_prev, v_curr) pairs
  - For each pair, load SKILL.md content from both versions
  - Record changelog text

Output: clawhub/data/version_transitions.csv
        clawhub/data/version_transitions_enriched.jsonl  (with full text)
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).parent
ROOT = HERE.parent.parent.parent  # clawhub/ → skillrugpull/ → experiments/ → project root
CLAWHUB_DB = ROOT / "data/experiment1_split/merged.sqlite3"
DATA_DIR = HERE / "data"
DATA_DIR.mkdir(exist_ok=True)

OUTPUT_CSV = DATA_DIR / "version_transitions.csv"
OUTPUT_JSONL = DATA_DIR / "version_transitions_enriched.jsonl"
MAX_TEXT_LEN = 8000  # chars to keep from SKILL.md per version


def load_file_text(text_path: str | None) -> str:
    """Load local text file content."""
    if not text_path:
        return ""
    full = ROOT / text_path if not os.path.isabs(text_path) else Path(text_path)
    if not full.exists():
        return ""
    try:
        return full.read_text(encoding="utf-8", errors="replace")[:MAX_TEXT_LEN]
    except Exception:
        return ""


def parse_time(ts: str | None) -> datetime | None:
    if not ts:
        return None
    for fmt in ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S"]:
        try:
            return datetime.fromisoformat(ts)
        except Exception:
            pass
    return None


def main() -> None:
    print(f"[clawhub/01_build_version_transitions] DB: {CLAWHUB_DB}")
    db = sqlite3.connect(str(CLAWHUB_DB))
    db.row_factory = sqlite3.Row

    # Get all multi-version skills
    multi_skills = db.execute("""
        SELECT skill_id, COUNT(*) as cnt
        FROM skill_versions
        GROUP BY skill_id
        HAVING cnt > 1
        ORDER BY cnt DESC
    """).fetchall()
    print(f"  Multi-version skills: {len(multi_skills):,}")

    # Load skill metadata
    skills_meta = {
        r["skill_id"]: dict(r)
        for r in db.execute("SELECT skill_id, name, description, author_handle, category, downloads, installs_all_time, current_version FROM skills").fetchall()
    }

    # Load all version records
    all_versions_raw = db.execute("""
        SELECT sv.skill_id, sv.version, sv.release_time, sv.changelog,
               sv.bundle_url, sv.security_has_warnings, sv.scan_status,
               -- SKILL.md file for this version
               sf_skill.text_path as skill_md_path,
               sf_readme.text_path as readme_path
        FROM skill_versions sv
        LEFT JOIN skill_files sf_skill
            ON sf_skill.skill_id = sv.skill_id AND sf_skill.version = sv.version
            AND sf_skill.path = 'SKILL.md'
        LEFT JOIN skill_files sf_readme
            ON sf_readme.skill_id = sv.skill_id AND sf_readme.version = sv.version
            AND sf_readme.path = 'README.md'
        WHERE sv.skill_id IN (
            SELECT skill_id FROM skill_versions GROUP BY skill_id HAVING COUNT(*) > 1
        )
        ORDER BY sv.skill_id, sv.release_time, sv.version
    """).fetchall()
    db.close()

    # Group by skill_id
    from collections import defaultdict
    skill_versions: dict[str, list[dict]] = defaultdict(list)
    for r in all_versions_raw:
        skill_versions[r["skill_id"]].append(dict(r))

    csv_rows = []
    jsonl_rows = []
    n_pairs = 0
    n_has_text = 0

    for skill_id, versions in skill_versions.items():
        # Sort by release_time then version string
        versions.sort(key=lambda v: (str(v.get("release_time") or ""), str(v.get("version") or "")))
        meta = skills_meta.get(skill_id, {})

        for i in range(len(versions) - 1):
            prev = versions[i]
            curr = versions[i + 1]

            # Load text content
            prev_skill_text = load_file_text(prev.get("skill_md_path"))
            curr_skill_text = load_file_text(curr.get("skill_md_path"))
            if not prev_skill_text:
                prev_skill_text = load_file_text(prev.get("readme_path"))
            if not curr_skill_text:
                curr_skill_text = load_file_text(curr.get("readme_path"))

            has_text = bool(prev_skill_text and curr_skill_text)
            changelog = curr.get("changelog") or ""

            time_delta_days = None
            t1 = parse_time(str(prev.get("release_time") or ""))
            t2 = parse_time(str(curr.get("release_time") or ""))
            if t1 and t2:
                try:
                    time_delta_days = round((t2 - t1).total_seconds() / 86400, 1)
                except Exception:
                    pass

            pair_id = f"{skill_id}::{prev['version']}→{curr['version']}"

            csv_row = {
                "pair_id": pair_id,
                "skill_id": skill_id,
                "author_handle": meta.get("author_handle", ""),
                "category": meta.get("category", ""),
                "downloads": meta.get("downloads", 0),
                "installs_all_time": meta.get("installs_all_time", 0),
                "description": meta.get("description", ""),
                "v_prev": prev["version"],
                "v_curr": curr["version"],
                "release_time_prev": prev.get("release_time", ""),
                "release_time_curr": curr.get("release_time", ""),
                "time_delta_days": time_delta_days,
                "changelog": changelog,
                "has_skill_text": int(has_text),
                "prev_skill_md_len": len(prev_skill_text),
                "curr_skill_md_len": len(curr_skill_text),
                "security_warnings_curr": curr.get("security_has_warnings", ""),
                "scan_status_curr": curr.get("scan_status", ""),
                # Feature columns (to be filled by 02_extract_drift_features)
                "ces_drift": "",
                "pes_drift": "",
                "sds": "",
                "changelog_vague": "",
                "label_candidate": "",
            }
            csv_rows.append(csv_row)

            if has_text:
                n_has_text += 1
                jsonl_rows.append({
                    "pair_id": pair_id,
                    "skill_id": skill_id,
                    "description": meta.get("description", ""),
                    "changelog": changelog,
                    "prev_version": prev["version"],
                    "curr_version": curr["version"],
                    "prev_skill_text": prev_skill_text,
                    "curr_skill_text": curr_skill_text,
                    "time_delta_days": time_delta_days,
                })

            n_pairs += 1

    # Write CSV
    if csv_rows:
        fieldnames = list(csv_rows[0].keys())
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)

    # Write JSONL (enriched with full text)
    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        for row in jsonl_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"  Total transition pairs: {n_pairs:,}")
    print(f"  Pairs with full SKILL.md text: {n_has_text:,}")
    print(f"  CSV: {OUTPUT_CSV}")
    print(f"  JSONL: {OUTPUT_JSONL}")


if __name__ == "__main__":
    main()
