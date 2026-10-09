#!/usr/bin/env python3
"""
10_case_studies.py
------------------
Generate detailed case studies for the top SkillRugPull candidates.
These become the qualitative evidence section of the paper.

For each top candidate:
  - Skill name, owner, description
  - Risk signals found in skill_text
  - What capability keywords appear in skill_text but NOT in description
  - Permission flags
  - Why this is a SkillRugPull candidate

Requires: data/feature_matrix.csv, data/disclosure_adequacy.csv,
          and access to DB for full text

Output: results/case_studies.md
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from config import DB_PATH, DATA_DIR, RESULTS_DIR, RISK_FAMILIES

MATRIX_PATH = DATA_DIR / "feature_matrix.csv"
DISCLOSURE_PATH = DATA_DIR / "disclosure_adequacy.csv"
REPORT_PATH = RESULTS_DIR / "case_studies.md"

N_CASE_STUDIES = 10
MAX_TEXT_PREVIEW = 400  # chars


def normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def load_csv(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["skill_id"]: r for r in csv.DictReader(f)}


def find_undisclosed_keywords(description: str, skill_text: str) -> dict[str, list[str]]:
    """Return per-family keywords present in skill_text but absent from description."""
    desc_norm = normalize(description).lower()
    skill_norm = normalize(skill_text).lower()
    result: dict[str, list[str]] = {}
    for family, keywords in RISK_FAMILIES.items():
        in_skill = [kw for kw in keywords if kw in skill_norm]
        undisclosed = [kw for kw in in_skill if kw not in desc_norm]
        if undisclosed:
            result[family] = undisclosed
    return result


def format_case_study(idx: int, skill: dict, row: dict, discl: dict) -> list[str]:
    """Format a single case study as markdown."""
    sid = skill["skill_id"]
    desc = normalize(skill.get("description") or "")
    skill_text = normalize(skill.get("skill_text") or "")
    summary = normalize(skill.get("summary_text") or "")
    full_text = skill_text + " " + summary

    undisclosed = find_undisclosed_keywords(desc, full_text)
    risk_exp = float(row.get("risk_expansion", 0) or 0)
    sds = float(row.get("sds", 0) or 0)
    rps = float(row.get("rps", 0) or 0)
    disclosure_adq = float(discl.get("disclosure_adequacy", 0) or 0)

    perm_flags = []
    perm_map = {
        "perm_requires_shell_access": "shell_access",
        "perm_requires_token": "token",
        "perm_requires_api_key": "api_key",
        "perm_requires_oauth": "oauth",
        "perm_requires_browser_access": "browser_access",
        "perm_requires_github": "github",
        "perm_requires_email": "email",
        "perm_requires_local_file_access": "local_file_access",
    }
    for col, label in perm_map.items():
        if int(float(row.get(col, 0) or 0)) == 1:
            perm_flags.append(label)

    lines = [
        f"### Case Study {idx}: `{sid}`",
        "",
        f"**Owner**: {skill.get('owner', 'unknown')} | **Repo**: {skill.get('repo', 'unknown')}",
        "",
        f"**Scores**: RPS={rps:.3f} | RiskExpansion={risk_exp:.3f} | SDS={sds:.3f} | DisclosureAdequacy={disclosure_adq:.3f}",
        "",
        "**User-visible description** (what users see before installing):",
        "",
        f"> {desc[:MAX_TEXT_PREVIEW]}{'...' if len(desc) > MAX_TEXT_PREVIEW else ''}",
        "",
        "**Permission flags inferred**: " + (", ".join(f"`{p}`" for p in perm_flags) if perm_flags else "*none*"),
        "",
        "**Undisclosed risk capabilities** (present in `skill_text` but NOT in `description`):",
        "",
    ]

    if undisclosed:
        for family, kws in sorted(undisclosed.items()):
            lines.append(f"- **{family}**: {', '.join(f'`{kw}`' for kw in kws[:8])}")
    else:
        lines.append("- *(No specific undisclosed keywords detected)*")

    lines += [
        "",
        "**Skill text excerpt** (what the skill actually does):",
        "",
        f"> {full_text[:MAX_TEXT_PREVIEW]}{'...' if len(full_text) > MAX_TEXT_PREVIEW else ''}",
        "",
        "**Why SkillRugPull candidate**:",
        "",
    ]

    reasons = []
    if risk_exp >= 0.5:
        reasons.append(f"Very high risk expansion score ({risk_exp:.2f})")
    if sds <= 0.2:
        reasons.append(f"Very low disclosure score ({sds:.2f}) — description severely understates capabilities")
    if any(kw in full_text.lower() for kw in ["shell", "exec(", "subprocess"]):
        reasons.append("Capable of shell/command execution not mentioned in description")
    if any(kw in full_text.lower() for kw in ["api_key", "token", "secret", "credential"]):
        reasons.append("Handles credentials/secrets without explicit disclosure")
    if any(kw in full_text.lower() for kw in ["requests.get", "requests.post", "fetch(", "http"]):
        reasons.append("Makes external network requests not mentioned in description")
    if not reasons:
        reasons.append(f"Combined risk expansion ({risk_exp:.2f}) exceeds disclosure adequacy ({disclosure_adq:.2f})")

    for r in reasons:
        lines.append(f"- {r}")

    lines += ["", "---", ""]
    return lines


def main() -> None:
    print("[10_case_studies] Loading data...")
    if not MATRIX_PATH.exists():
        print("  ERROR: Run 08_build_feature_matrix.py first")
        return

    matrix = load_csv(MATRIX_PATH)
    disclosure = load_csv(DISCLOSURE_PATH)

    # Sort by RPS descending
    sorted_rows = sorted(
        matrix.values(),
        key=lambda r: float(r.get("rps", 0) or 0),
        reverse=True
    )
    top_candidates = [r for r in sorted_rows if int(float(r.get("label", 0) or 0)) == 1][:N_CASE_STUDIES]

    if not top_candidates:
        top_candidates = sorted_rows[:N_CASE_STUDIES]
        print("  WARNING: No labeled candidates found — using top RPS scores")

    print(f"  Fetching skill texts for {len(top_candidates)} case studies from DB...")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    skill_ids = [r["skill_id"] for r in top_candidates]
    placeholders = ",".join("?" * len(skill_ids))
    skills_rows = db.execute(
        f"SELECT * FROM skills WHERE skill_id IN ({placeholders})", skill_ids
    ).fetchall()
    db.close()
    skills_map = {s["skill_id"]: dict(s) for s in skills_rows}

    lines = [
        "# SkillRugPull: Case Studies",
        "",
        f"Top **{len(top_candidates)}** SkillRugPull candidates by RPS score.",
        "",
        "> Each case study shows: user-visible description, actual capabilities in skill_text,",
        "> permission flags, undisclosed risk keywords, and why the skill qualifies as a candidate.",
        "",
        "---",
        "",
    ]

    for idx, row in enumerate(top_candidates, 1):
        sid = row["skill_id"]
        skill = skills_map.get(sid, {"skill_id": sid, "owner": row.get("owner", ""), "repo": row.get("repo", ""), "description": "", "skill_text": "", "summary_text": ""})
        discl = disclosure.get(sid, {})
        lines.extend(format_case_study(idx, skill, row, discl))

    lines += [
        "## Summary",
        "",
        "These case studies demonstrate the **SkillRugPull** pattern:",
        "",
        "1. **Description mismatch**: The user-visible description omits or understates key risky capabilities",
        "2. **Implicit permissions**: Shell access, credential handling, and network operations are buried in skill_text",
        "3. **Approval scope ambiguity**: A user who approved the skill based on its description",
        "   may have consented to a much narrower capability set than the skill actually exercises",
        "4. **TOCTOU analogy**: 'What you approved' (description) ≠ 'What runs' (skill_text operations)",
    ]

    RESULTS_DIR.mkdir(exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Generated {len(top_candidates)} case studies → {REPORT_PATH}")


if __name__ == "__main__":
    main()
