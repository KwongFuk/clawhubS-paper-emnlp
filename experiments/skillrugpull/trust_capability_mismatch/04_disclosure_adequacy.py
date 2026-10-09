#!/usr/bin/env python3
"""
04_disclosure_adequacy.py
-------------------------
Compute the Semantic Disclosure Score (SDS) and disclosure adequacy
metrics for every skill. This enumerates what risky capabilities the
skill_text claims to have, then measures how well those are surfaced
in the user-visible description.

Also detects "vague over-promise" patterns: descriptions that use marketing
language while skill_text implies high-risk operations.

Joins features from 02_extract_features.py (if available) with additional
disclosure metrics.

Output: data/disclosure_adequacy.csv
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from config import DB_PATH, DATA_DIR, RISK_FAMILIES, VAGUE_DISCLOSURE_PHRASES

OUTPUT_PATH = DATA_DIR / "disclosure_adequacy.csv"

# Marketing / safe-sounding words often used in descriptions of risky skills
MARKETING_PHRASES = [
    "seamlessly", "effortlessly", "simple", "easy", "intuitive",
    "one-click", "powerful", "smart", "intelligent", "helpful",
    "private", "secure", "safe", "without leaving", "zero configuration",
]

# Explicit risk-disclosure phrases — presence in description is a GOOD sign
RISK_DISCLOSURE_PHRASES = [
    "requires shell", "runs commands", "executes", "access your files",
    "reads your", "writes to", "sends data", "external api", "api key required",
    "authentication required", "oauth", "network access", "internet access",
    "browser automation", "controls browser",
]


def normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def fact_extraction(skill_text: str) -> list[dict]:
    """
    Extract 'risky fact' structs from skill_text — each fact is a family + matched keywords.
    """
    skill_norm = normalize(skill_text)
    facts = []
    for family, keywords in RISK_FAMILIES.items():
        matched = [kw for kw in keywords if kw in skill_norm]
        if matched:
            facts.append({"family": family, "matched_keywords": matched, "count": len(matched)})
    return facts


def disclosure_coverage(facts: list[dict], description: str) -> dict[str, float | int]:
    """
    For each risky fact (family + keywords), check whether description covers it.
    """
    desc_norm = normalize(description)
    n_facts = len(facts)
    if n_facts == 0:
        return {
            "n_risky_facts": 0,
            "n_disclosed_facts": 0,
            "fact_coverage": 1.0,
            "risk_families_found": "",
        }

    disclosed = 0
    families_found = []
    for fact in facts:
        kws = fact["matched_keywords"]
        # A fact is "disclosed" if at least one of its keywords appears in description
        if any(kw in desc_norm for kw in kws):
            disclosed += 1
        families_found.append(fact["family"])

    coverage = disclosed / n_facts
    return {
        "n_risky_facts": n_facts,
        "n_disclosed_facts": disclosed,
        "fact_coverage": round(coverage, 4),
        "risk_families_found": "|".join(families_found),
    }


def vagueness_analysis(description: str, skill_text: str) -> dict[str, float | int]:
    """
    Detect if description is full of marketing language while skill_text is risky.
    """
    desc_norm = normalize(description)
    skill_norm = normalize(skill_text)

    marketing_count = sum(1 for ph in MARKETING_PHRASES if ph in desc_norm)
    explicit_risk_disclosure = sum(1 for ph in RISK_DISCLOSURE_PHRASES if ph in desc_norm)
    vague_count = sum(1 for ph in VAGUE_DISCLOSURE_PHRASES if ph in desc_norm)

    # How risky is the skill_text?
    total_risk_hits = sum(
        sum(1 for kw in keywords if kw in skill_norm)
        for keywords in RISK_FAMILIES.values()
    )

    # Vague score = high marketing + low explicit disclosure + high skill risk
    mismatch_score = 0.0
    if total_risk_hits > 0:
        explicit_ratio = explicit_risk_disclosure / max(marketing_count + 1, 1)
        mismatch_score = min(
            (marketing_count * 0.1 + vague_count * 0.15 - explicit_ratio * 0.2),
            1.0
        )
        mismatch_score = max(mismatch_score, 0.0)

    return {
        "marketing_phrase_count": marketing_count,
        "explicit_risk_disclosure_count": explicit_risk_disclosure,
        "vague_changelog_count": vague_count,
        "skill_text_risk_hits": total_risk_hits,
        "vague_mismatch_score": round(mismatch_score, 4),
    }


def compute_final_adequacy(
    fact_coverage: float,
    vague_mismatch_score: float,
    explicit_risk_disclosure_count: int,
    n_risky_facts: int,
) -> float:
    """
    Final disclosure adequacy score ∈ [0, 1].
    Higher = better disclosure.
    """
    if n_risky_facts == 0:
        # No risky capabilities → trivially adequate
        return 1.0

    bonus_explicit = min(explicit_risk_disclosure_count * 0.1, 0.2)
    adequacy = (
        0.6 * fact_coverage
        + bonus_explicit
        - 0.4 * vague_mismatch_score
    )
    return round(max(0.0, min(adequacy, 1.0)), 4)


def main() -> None:
    print(f"[04_disclosure_adequacy] Reading from {DB_PATH}")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    skills = db.execute(
        "SELECT skill_id, owner, repo, description, summary_text, skill_text, scan_status "
        "FROM skills WHERE detail_complete = 1"
    ).fetchall()
    db.close()
    print(f"  Analyzing disclosure adequacy for {len(skills):,} skills...")

    fieldnames: list[str] | None = None
    rows_out: list[dict] = []

    for skill in skills:
        full_skill_text = (skill["skill_text"] or "") + " " + (skill["summary_text"] or "")
        description = skill["description"] or ""

        facts = fact_extraction(full_skill_text)
        cov = disclosure_coverage(facts, description)
        vague = vagueness_analysis(description, full_skill_text)

        final_adequacy = compute_final_adequacy(
            fact_coverage=float(cov["fact_coverage"]),
            vague_mismatch_score=float(vague["vague_mismatch_score"]),
            explicit_risk_disclosure_count=int(vague["explicit_risk_disclosure_count"]),
            n_risky_facts=int(cov["n_risky_facts"]),
        )

        # Classification
        if cov["n_risky_facts"] == 0:
            disclosure_class = "no_risk"
        elif final_adequacy >= 0.7:
            disclosure_class = "adequate"
        elif final_adequacy >= 0.35:
            disclosure_class = "partial"
        else:
            disclosure_class = "inadequate"

        row = {
            "skill_id": skill["skill_id"],
            "owner": skill["owner"],
            "repo": skill["repo"],
            "scan_status": skill["scan_status"] or "",
            **cov,
            **vague,
            "disclosure_adequacy": final_adequacy,
            "disclosure_class": disclosure_class,
        }

        if fieldnames is None:
            fieldnames = list(row.keys())
        rows_out.append(row)

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames or [])
        writer.writeheader()
        writer.writerows(rows_out)

    # Summary stats
    class_counts = {}
    for r in rows_out:
        cls = r["disclosure_class"]
        class_counts[cls] = class_counts.get(cls, 0) + 1
    print("  Disclosure class distribution:")
    for cls, cnt in sorted(class_counts.items()):
        print(f"    {cls}: {cnt:,} ({100*cnt/max(len(rows_out),1):.1f}%)")
    print(f"  Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
