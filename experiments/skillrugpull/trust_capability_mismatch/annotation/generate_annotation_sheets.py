#!/usr/bin/env python3
"""
annotation/generate_annotation_sheets.py
-----------------------------------------
Generate human-readable annotation sheets from the sample CSV.
Each row gets enriched with full text excerpts from the DB for annotators.

Output: annotation/annotation_sheets_enriched.csv  (full content for annotators)
        annotation/annotation_instructions.md
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))
from config import DB_PATH, ANNOTATION_DIR, RISK_FAMILIES

SAMPLE_PATH = ANNOTATION_DIR / "annotation_sample_500.csv"
OUTPUT_PATH = ANNOTATION_DIR / "annotation_sheets_enriched.csv"
INSTRUCTIONS_PATH = ANNOTATION_DIR / "annotation_instructions.md"

MAX_EXCERPT = 600


def normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def excerpt(text: str, n: int = MAX_EXCERPT) -> str:
    text = normalize(text)
    return text[:n] + ("..." if len(text) > n else "")


def find_undisclosed(description: str, skill_text: str) -> str:
    desc_norm = normalize(description).lower()
    skill_norm = normalize(skill_text).lower()
    parts = []
    for family, keywords in RISK_FAMILIES.items():
        hits = [kw for kw in keywords if kw in skill_norm and kw not in desc_norm]
        if hits:
            parts.append(f"{family}: {', '.join(hits[:5])}")
    return " | ".join(parts) if parts else "none detected"


def write_instructions() -> None:
    lines = [
        "# SkillRugPull Annotation Instructions",
        "",
        "## Overview",
        "You are annotating LLM agent skills for the **SkillRugPull** study.",
        "We are identifying skills where the user-visible description **understates** the actual",
        "capabilities — creating a trust gap between what the user approved and what runs.",
        "",
        "## Your Task (per skill)",
        "",
        "### Column: `label_risk_expansion`",
        "Read the **skill_text_excerpt** (what the skill actually does). Then rate:",
        "",
        "- **0** = No significant expansion — description matches capabilities",
        "- **1** = Minor expansion — some undisclosed capabilities but low risk",
        "- **2** = Significant expansion — shell/exec/token/network/external-action capabilities in skill_text but NOT in description",
        "",
        "**Tip**: Look at `undisclosed_keywords` column for pre-computed mismatches.",
        "",
        "### Column: `label_disclosure`",
        "Read the **description_excerpt** (what users see). Rate adequacy:",
        "",
        "- **0** = Inadequate — description conceals or ignores major risk-relevant capabilities",
        "- **1** = Partial — some risk mentioned but key ones omitted",
        "- **2** = Adequate — description fairly represents skill's capabilities",
        "",
        "### Column: `label_rugpull`",
        "Final binary label:",
        "",
        "- **0** = Not a SkillRugPull candidate",
        "- **1** = SkillRugPull candidate (assign 1 if label_risk_expansion=2 AND label_disclosure=0)",
        "",
        "### Column: `annotator_notes`",
        "Optional free-text notes about why you labeled it as you did.",
        "",
        "## What to Look For",
        "",
        "| Risk Signal | Example keywords |",
        "|-------------|-----------------|",
        "| Shell execution | `shell`, `bash`, `subprocess`, `exec(`, `os.system` |",
        "| Credential access | `api_key`, `token`, `secret`, `credential`, `oauth` |",
        "| Network requests | `requests.get`, `fetch(`, `webhook`, `external api` |",
        "| File system | `write file`, `upload`, `download`, `open(` |",
        "| External actions | `send email`, `create PR`, `deploy`, `browser automation` |",
        "| Autonomous agent | `autonomous`, `plan and execute`, `memory`, `loop until` |",
        "",
        "## Inter-annotator Agreement",
        "After annotation, Cohen's κ will be computed between annotators.",
        "Disagreements on `label_rugpull` items will be arbitrated by a third reviewer.",
        "",
        "## Questions?",
        "If a skill's description or skill_text is empty, mark `label_risk_expansion=0`,",
        "`label_disclosure=2`, `label_rugpull=0`, and note in `annotator_notes`.",
    ]
    with open(INSTRUCTIONS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    if not SAMPLE_PATH.exists():
        print(f"  ERROR: {SAMPLE_PATH} not found — run sampling.py first")
        return

    with open(SAMPLE_PATH, newline="", encoding="utf-8") as f:
        sample_rows = list(csv.DictReader(f))

    skill_ids = [r["skill_id"] for r in sample_rows]
    print(f"[generate_annotation_sheets] Fetching {len(skill_ids)} skills from DB...")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(skill_ids))
    skills = db.execute(
        f"SELECT skill_id, description, skill_text, summary_text FROM skills WHERE skill_id IN ({placeholders})",
        skill_ids,
    ).fetchall()
    db.close()

    skills_map = {s["skill_id"]: dict(s) for s in skills}

    enriched_rows = []
    for row in sample_rows:
        sid = row["skill_id"]
        sk = skills_map.get(sid, {})
        desc = sk.get("description") or ""
        skill_text = sk.get("skill_text") or ""
        summary = sk.get("summary_text") or ""
        full_text = skill_text + " " + summary

        enriched = {
            **row,
            "description_excerpt": excerpt(desc),
            "skill_text_excerpt": excerpt(full_text),
            "undisclosed_keywords": find_undisclosed(desc, full_text),
        }
        enriched_rows.append(enriched)

    out_fields = [
        "skill_id", "owner", "repo", "stratum",
        "rps", "risk_expansion", "sds",
        "ces", "pes", "disclosure_adequacy",
        "perm_requires_shell_access", "perm_requires_token", "perm_requires_api_key",
        "ces_execution_count", "ces_secret_count", "ces_network_count",
        "semantic_gap", "risk_divergence_overall",
        "description_excerpt",
        "skill_text_excerpt",
        "undisclosed_keywords",
        "label_risk_expansion", "label_disclosure", "label_rugpull",
        "annotator_notes",
    ]

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(enriched_rows)

    write_instructions()
    print(f"  Enriched annotation sheet: {OUTPUT_PATH}")
    print(f"  Annotation instructions: {INSTRUCTIONS_PATH}")


if __name__ == "__main__":
    main()
