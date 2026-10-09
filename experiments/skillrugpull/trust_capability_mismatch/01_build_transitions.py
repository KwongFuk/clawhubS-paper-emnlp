#!/usr/bin/env python3
"""
01_build_transitions.py
-----------------------
Build version transition table from skill_versions.
Each row is a (skill_id, old_version, new_version) transition pair.

Since the current DB only stores (skill_id, version) with no timestamps or
changelogs, this script:
  1. Constructs all adjacent version pairs per skill (sorted lexicographically).
  2. Emits a "snapshot" mode for single-version skills (no real transitions).
  3. Attempts to enrich with changelog data from the skills.sh API where possible.

Output: data/transitions.csv
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from pathlib import Path
from collections import defaultdict
from urllib import request as urllib_request, error as urllib_error

# ── path setup ───────────────────────────────────────────────────────────────
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from config import DB_PATH, DATA_DIR

OUTPUT_PATH = DATA_DIR / "transitions.csv"
SNAPSHOT_PATH = DATA_DIR / "skill_snapshots.csv"


def parse_semver_key(v: str) -> tuple:
    """Return a sort key for version strings (semver or lexicographic fallback)."""
    parts = re.split(r"[.\-]", v)
    key = []
    for p in parts:
        try:
            key.append((0, int(p)))
        except ValueError:
            key.append((1, p.lower()))
    return tuple(key)


def fetch_changelog(skill_id: str) -> dict[str, str]:
    """
    Attempt to fetch version changelog from skills.sh API.
    Returns {version: changelog_text} or empty dict on failure.
    """
    # skills.sh e.g. https://skills.sh/owner/repo/skill?format=json  (not guaranteed to exist)
    url = f"https://skills.sh/{skill_id}?format=json"
    try:
        req = urllib_request.Request(
            url, headers={"User-Agent": "clawhub-analyzer/0.1 (+research)"}
        )
        with urllib_request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            versions = data.get("versions", [])
            return {v.get("version", ""): v.get("changelog", "") for v in versions}
    except Exception:
        return {}


def build_transitions(db: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """
    Returns (transitions, snapshots).
    transitions: list of dicts with keys:
        skill_id, old_version, new_version, transition_index, total_versions, changelog
    snapshots: list of dicts for single-version or all-version skills (for static analysis)
    """
    rows = db.execute(
        "SELECT skill_id, version FROM skill_versions ORDER BY skill_id, version"
    ).fetchall()

    by_skill: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_skill[row["skill_id"]].append(row["version"])

    transitions: list[dict] = []
    snapshots: list[dict] = []

    for skill_id, versions in by_skill.items():
        # sort versions
        try:
            sorted_versions = sorted(versions, key=parse_semver_key)
        except Exception:
            sorted_versions = sorted(versions)

        # snapshot record (current = last version)
        snapshots.append(
            {
                "skill_id": skill_id,
                "latest_version": sorted_versions[-1],
                "version_count": len(sorted_versions),
                "all_versions": "|".join(sorted_versions),
            }
        )

        if len(sorted_versions) < 2:
            continue

        # build adjacent transitions
        for i in range(1, len(sorted_versions)):
            old_v = sorted_versions[i - 1]
            new_v = sorted_versions[i]
            transitions.append(
                {
                    "skill_id": skill_id,
                    "old_version": old_v,
                    "new_version": new_v,
                    "transition_index": i,
                    "total_versions": len(sorted_versions),
                    "changelog": "",  # populated later if enrichment succeeds
                }
            )

    return transitions, snapshots


def main() -> None:
    print(f"[01_build_transitions] Reading from {DB_PATH}")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    transitions, snapshots = build_transitions(db)
    db.close()

    # Write snapshots (all skills, including single-version ones)
    with open(SNAPSHOT_PATH, "w", newline="", encoding="utf-8") as f:
        if snapshots:
            writer = csv.DictWriter(f, fieldnames=list(snapshots[0].keys()))
            writer.writeheader()
            writer.writerows(snapshots)
    print(f"  Wrote {len(snapshots):,} skill snapshots → {SNAPSHOT_PATH}")

    # Write transitions
    multi_skill_count = len({t["skill_id"] for t in transitions})
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        if transitions:
            writer = csv.DictWriter(f, fieldnames=list(transitions[0].keys()))
            writer.writeheader()
            writer.writerows(transitions)
        else:
            # No multi-version skills yet — write header only so downstream can proceed
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "skill_id", "old_version", "new_version",
                    "transition_index", "total_versions", "changelog"
                ],
            )
            writer.writeheader()

    print(
        f"  Wrote {len(transitions):,} transitions from "
        f"{multi_skill_count:,} multi-version skills → {OUTPUT_PATH}"
    )

    if not transitions:
        print(
            "\n  NOTE: No multi-version skills found in DB. This is expected for the\n"
            "  current snapshot. Static snapshot analysis will proceed using the\n"
            "  skill_snapshots.csv file. To collect real version transitions, run a\n"
            "  targeted crawl of skills.sh version history pages.\n"
        )


if __name__ == "__main__":
    main()
