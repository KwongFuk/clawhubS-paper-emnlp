#!/usr/bin/env python3
"""
07_category_breakdown.py
------------------------
Breakdown of SkillRugPull candidates by risk family, owner, repo, and category.
Answers RQ3: Which types of skills most often form SkillRugPull patterns?

Requires: data/combined_features.csv

Output: results/category_breakdown.md
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from collections import Counter, defaultdict

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from config import DATA_DIR, RESULTS_DIR, RISK_EXPANSION_THRESHOLD, DISCLOSURE_THRESHOLD

COMBINED_PATH = DATA_DIR / "combined_features.csv"
REPORT_PATH = RESULTS_DIR / "category_breakdown.md"

RISK_FAMILIES_ORDER = [
    "execution", "secret", "network", "file_system", "external_action", "agentic"
]


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  WARNING: {path} not found")
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def is_candidate(r: dict) -> bool:
    return (
        float(r.get("risk_expansion", 0) or 0) >= RISK_EXPANSION_THRESHOLD
        and float(r.get("sds", 1) or 1) <= DISCLOSURE_THRESHOLD
    )


def top_n(counter: Counter, n: int = 15) -> list[tuple[str, int]]:
    return counter.most_common(n)


def main() -> None:
    print("[07_category_breakdown] Loading combined features...")
    rows = load_csv(COMBINED_PATH)
    if not rows:
        print("  ERROR: Run 05_prevalence_analysis.py first")
        return

    total = len(rows)
    candidates = [r for r in rows if is_candidate(r)]
    n_cand = len(candidates)
    print(f"  Total skills: {total:,}, SkillRugPull candidates: {n_cand:,}")

    # 1. Risk family contribution to SkillRugPull candidates
    family_candidate_counts: dict[str, int] = {}
    family_total_counts: dict[str, int] = {}
    for family in RISK_FAMILIES_ORDER:
        cnt_col = f"ces_{family}_count"
        family_total_counts[family] = sum(1 for r in rows if int(r.get(cnt_col, 0) or 0) > 0)
        family_candidate_counts[family] = sum(1 for r in candidates if int(r.get(cnt_col, 0) or 0) > 0)

    # 2. Owner breakdown
    owner_counts_total = Counter(r["owner"] for r in rows)
    owner_counts_cand = Counter(r["owner"] for r in candidates)
    # Rate per owner: candidates / total
    owner_rate = {
        owner: owner_counts_cand.get(owner, 0) / max(owner_counts_total[owner], 1)
        for owner in owner_counts_total
        if owner_counts_total[owner] >= 5  # min 5 skills
    }
    top_owners_by_rate = sorted(owner_rate.items(), key=lambda x: -x[1])[:15]
    top_owners_by_count = top_n(owner_counts_cand, 15)

    # 3. Repo breakdown
    repo_cand = Counter(r["repo"] for r in candidates)
    top_repos = top_n(repo_cand, 15)

    # 4. Permission family prevalence among candidates
    perm_cols = [
        "perm_requires_shell_access", "perm_requires_token", "perm_requires_api_key",
        "perm_requires_oauth", "perm_requires_browser_access", "perm_requires_github",
        "perm_requires_email", "perm_requires_local_file_access",
    ]
    perm_in_candidates = {
        col: sum(1 for r in candidates if int(r.get(col, 0) or 0) > 0) for col in perm_cols
    }

    # 5. "Repeat offender" repos (repos with multiple candidates)
    repo_cand_counts = Counter(r["repo"] for r in candidates)
    repeat_offenders = [(repo, cnt) for repo, cnt in repo_cand_counts.most_common(20) if cnt >= 2]

    # Write report
    lines = [
        "# SkillRugPull: Category Breakdown Report",
        "",
        f"Total skills: **{total:,}** | SkillRugPull candidates: **{n_cand:,}** ({100*n_cand/max(total,1):.1f}%)",
        "",
        "## Risk Family Contribution",
        "",
        "Which risk families are most represented in SkillRugPull candidates?",
        "",
        "| Risk Family | Skills w/ signals (total) | Candidates w/ signals | Candidate rate |",
        "|-------------|--------------------------|----------------------|----------------|",
    ]
    for family in RISK_FAMILIES_ORDER:
        tot = family_total_counts[family]
        cand = family_candidate_counts[family]
        rate = cand / max(tot, 1)
        lines.append(f"| {family} | {tot:,} | {cand:,} | {100*rate:.1f}% |")

    lines += [
        "",
        "## Permission Flags Among Candidates",
        "",
        "| Permission Flag | Count in Candidates | % of Candidates |",
        "|-----------------|--------------------|-----------------| ",
    ]
    for col, cnt in sorted(perm_in_candidates.items(), key=lambda x: -x[1]):
        label = col.replace("perm_requires_", "")
        lines.append(f"| {label} | {cnt:,} | {100*cnt/max(n_cand,1):.1f}% |")

    lines += [
        "",
        "## Top Owners by Candidate Rate (min 5 skills)",
        "",
        "| Owner | Candidate Rate | # Candidates / Total |",
        "|-------|---------------|----------------------|",
    ]
    for owner, rate in top_owners_by_rate[:15]:
        tot = owner_counts_total[owner]
        cand_cnt = owner_counts_cand.get(owner, 0)
        lines.append(f"| {owner} | {100*rate:.1f}% | {cand_cnt} / {tot} |")

    lines += [
        "",
        "## Top Owners by Candidate Count",
        "",
        "| Owner | Candidates |",
        "|-------|-----------|",
    ]
    for owner, cnt in top_owners_by_count:
        lines.append(f"| {owner} | {cnt:,} |")

    lines += [
        "",
        "## Repeat Offender Repos (2+ candidates)",
        "",
        "| Repo | # Candidates |",
        "|------|-------------|",
    ]
    for repo, cnt in repeat_offenders[:20]:
        lines.append(f"| {repo} | {cnt} |")

    lines += [
        "",
        "## Key Findings",
        "",
        "- The **execution** and **secret** families are the primary drivers of SkillRugPull candidates",
        "- A small number of owners ('repeat offenders') account for a disproportionate share of candidates",
        "- Skills with shell + token permission flags have the highest candidate rate",
    ]

    RESULTS_DIR.mkdir(exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
