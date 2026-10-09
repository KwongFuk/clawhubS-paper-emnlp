#!/usr/bin/env python3
"""
12_skillssh_declaration_mismatch.py
-----------------------------------
Supplementary analysis for skills.sh using the same core methodology as the
main snapshot pipeline:

- risk_expansion from CES + PES
- disclosure mismatch from SDS / disclosure_adequacy
- candidate label from the existing feature matrix
- qualitative support from undisclosed keywords and permission hints
- optional calibration from skillssh_annotations.csv

This script does NOT introduce a new metric. It reframes existing static
capability-description mismatch results as "declaration/identity inconsistency"
evidence for skills.sh.
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from config import DATA_DIR, RESULTS_DIR, DB_PATH, RISK_FAMILIES, RISK_EXPANSION_THRESHOLD, DISCLOSURE_THRESHOLD

MATRIX_PATH = DATA_DIR / "feature_matrix.csv"
DISCLOSURE_PATH = DATA_DIR / "disclosure_adequacy.csv"
ANNOTATION_PATH = DATA_DIR / "skillssh_annotations.csv"
REPORT_PATH = RESULTS_DIR / "skillssh_declaration_mismatch.md"

TOP_K = 12
MAX_KWS_PER_FAMILY = 5


def load_csv_map(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["skill_id"]: row for row in csv.DictReader(handle)}


def load_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def find_undisclosed_keywords(description: str, skill_text: str) -> dict[str, list[str]]:
    desc_norm = normalize(description)
    skill_norm = normalize(skill_text)
    result: dict[str, list[str]] = {}
    for family, keywords in RISK_FAMILIES.items():
        in_skill = [kw for kw in keywords if kw in skill_norm]
        hidden = [kw for kw in in_skill if kw not in desc_norm]
        if hidden:
            result[family] = hidden[:MAX_KWS_PER_FAMILY]
    return result


def permission_flags(row: dict) -> list[str]:
    out: list[str] = []
    for key, label in [
        ("perm_requires_shell_access", "shell"),
        ("perm_requires_token", "token"),
        ("perm_requires_api_key", "api_key"),
        ("perm_requires_oauth", "oauth"),
        ("perm_requires_browser_access", "browser"),
        ("perm_requires_email", "email"),
        ("perm_requires_local_file_access", "local_file"),
        ("perm_requires_github", "github"),
    ]:
        if int(float(row.get(key, 0) or 0)) == 1:
            out.append(label)
    return out


def fetch_skill_texts(skill_ids: list[str]) -> dict[str, dict]:
    if not skill_ids:
        return {}
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(skill_ids))
    rows = db.execute(
        f"SELECT skill_id, owner, repo, name, description, summary_text, skill_text "
        f"FROM skills WHERE skill_id IN ({placeholders})",
        skill_ids,
    ).fetchall()
    db.close()
    return {row["skill_id"]: dict(row) for row in rows}


def main() -> None:
    matrix_rows = load_csv_rows(MATRIX_PATH)
    if not matrix_rows:
        raise SystemExit(f"missing feature matrix: {MATRIX_PATH}")
    disclosure = load_csv_map(DISCLOSURE_PATH)
    annotations = load_csv_map(ANNOTATION_PATH)

    total = len(matrix_rows)
    high_risk = [r for r in matrix_rows if float(r.get("risk_expansion", 0) or 0) >= RISK_EXPANSION_THRESHOLD]
    candidates = [r for r in matrix_rows if int(float(r.get("label", 0) or 0)) == 1]
    candidates_sorted = sorted(candidates, key=lambda r: float(r.get("rps", 0) or 0), reverse=True)
    top = candidates_sorted[:TOP_K]

    skill_meta = fetch_skill_texts([row["skill_id"] for row in top])

    ann_counter = Counter()
    for row in annotations.values():
        ann_counter[row.get("final_label", "")] += 1

    hidden_family_counter = Counter()
    top_rows_rendered: list[str] = []
    for row in top:
        sid = row["skill_id"]
        meta = skill_meta.get(sid, {})
        desc = meta.get("description", "")
        full_text = (meta.get("skill_text", "") or "") + " " + (meta.get("summary_text", "") or "")
        hidden = find_undisclosed_keywords(desc, full_text)
        for fam in hidden:
            hidden_family_counter[fam] += 1
        hidden_text = "; ".join(
            f"{fam}: {', '.join(kws)}" for fam, kws in sorted(hidden.items())
        ) or "none detected"
        perms = ", ".join(permission_flags(row)) or "none"
        top_rows_rendered.append(
            f"| `{sid}` | {float(row.get('risk_expansion', 0) or 0):.3f} | "
            f"{float(row.get('sds', 0) or 0):.3f} | "
            f"{float(disclosure.get(sid, {}).get('disclosure_adequacy', 0) or 0):.3f} | "
            f"`{perms}` | {hidden_text} |"
        )

    hidden_lines = []
    for fam, cnt in hidden_family_counter.most_common():
        hidden_lines.append(f"- `{fam}` appears in **{cnt}/{len(top)}** top candidates as undisclosed capability family")

    report = [
        "# skills.sh Declaration/Capability Mismatch Supplement",
        "",
        "This report uses the same snapshot methodology already used in the main `experiments/skillrugpull` pipeline.",
        "",
        "## Static Summary",
        "",
        f"- Total skills analyzed: **{total:,}**",
        f"- High-risk skills (`risk_expansion ≥ {RISK_EXPANSION_THRESHOLD}`): **{len(high_risk):,}** ({100*len(high_risk)/max(total,1):.1f}%)",
        f"- Static mismatch candidates (`label = 1`): **{len(candidates):,}** ({100*len(candidates)/max(total,1):.1f}%)",
        f"- Among high-risk skills, **{100*len(candidates)/max(len(high_risk),1):.1f}%** have inadequate disclosure under the existing rule",
        "",
        "## Annotation Calibration",
        "",
        "- This section reuses `skillssh_annotations.csv` rather than inventing a new adjudication layer.",
        f"- Consensus `FALSE_POSITIVE`: **{ann_counter.get('FALSE_POSITIVE', 0):,}**",
        f"- Consensus `UNCLEAR`: **{ann_counter.get('UNCLEAR', 0):,}**",
        f"- Consensus `CONFIRMED`: **{ann_counter.get('CONFIRMED', 0):,}**",
        "",
        "Interpretation:",
        "",
        "- The same static mismatch method does surface declaration/capability inconsistencies on `skills.sh`.",
        "- But current evidence is still cross-sectional and over-flags heavily; these are best treated as mismatch candidates rather than confirmed malicious skills.",
        "",
        "## Top Static Mismatch Cases",
        "",
        "| Skill | RiskExpansion | SDS | DisclosureAdequacy | Permission flags | Undisclosed capability families / keywords |",
        "|------|---------------:|----:|-------------------:|------------------|-------------------------------------------|",
        *top_rows_rendered,
        "",
        "## Hidden Capability Families In Top Cases",
        "",
        *hidden_lines,
        "",
        "## Bottom Line",
        "",
        "> Using the same method as the main snapshot pipeline, `skills.sh` does exhibit the same basic problem we observed elsewhere: a subset of high-risk skills present a narrower user-facing declaration than the capabilities and permission surfaces visible in `skill_text` and permission hints. However, because `skills.sh` lacks true version-transition evidence in the current archive, this should be reported as static declaration/capability mismatch, not confirmed longitudinal trust drift.",
        "",
    ]

    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
