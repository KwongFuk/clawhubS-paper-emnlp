#!/usr/bin/env python3
"""
clawhub/12_reassurance_washing.py
-----------------------------------
Exp 4: Reassurance Washing Analysis

Finds skills that use "safe/simple/minimal/privacy" marketing language
in their description while having high actual capability gaps.

Key question: Is reassurance language INVERSELY correlated with
actual disclosure adequacy? If yes → evidence of intentional misleading.

Output:
  clawhub/data/reassurance_washing.csv
  clawhub/results/reassurance_washing_report.md
"""

from __future__ import annotations
import csv
import re
from pathlib import Path
from collections import Counter

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"
RESULTS_DIR.mkdir(exist_ok=True)

SDG_CSV = DATA_DIR / "structured_sdg.csv"
OUTPUT_CSV = DATA_DIR / "reassurance_washing.csv"
REPORT_PATH = RESULTS_DIR / "reassurance_washing_report.md"

# Reassurance vocabulary to analyze
REASSURANCE_GROUPS = {
    "safety": ["safe", "safely", "secure", "securely", "harmless", "non-destructive"],
    "scope": ["simple", "minimal", "lightweight", "only", "just", "read-only", "read only"],
    "privacy": ["privacy", "private", "no data collection", "does not store", "never sends",
                "local only", "offline", "no tracking"],
    "speed": ["fast", "quick", "efficient", "optimized"],  # control group — neutral words
}

RISK_LEVELS = {
    "SHELL_EXEC": 3,
    "CRED_TRANSMIT": 3,
    "CRED_STORE": 2,
    "FILE_DELETE": 2,
    "NET_WRITE": 2,
    "CRED_READ": 2,
    "AUTONOMOUS": 2,
    "NET_READ": 1,
    "FILE_READ": 1,
    "FILE_WRITE": 1,
    "MEMORY_WRITE": 1,
    "MULTI_STEP": 1,
}


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def count_group(text: str, words: list[str]) -> int:
    norm = text.lower()
    return sum(1 for w in words if w in norm)


def max_risk_level(caps_str: str) -> int:
    if not caps_str:
        return 0
    return max((RISK_LEVELS.get(c, 0) for c in caps_str.split("|") if c), default=0)


def main():
    print("[12_reassurance_washing] Loading SDG results...")
    if not SDG_CSV.exists():
        print(f"ERROR: {SDG_CSV} not found. Run 10_structured_sdg_analysis.py first.")
        return

    rows = load_csv(SDG_CSV)
    print(f"  Skills: {len(rows)}")

    # Need original descriptions - read from DB
    import sqlite3
    ROOT = HERE.parent.parent.parent
    db = sqlite3.connect(str(ROOT / "data/experiment1_split/merged.sqlite3"))
    db.row_factory = sqlite3.Row
    desc_map = {r["skill_id"]: r["description"] or ""
                for r in db.execute("SELECT skill_id, description FROM skills").fetchall()}
    db.close()

    results = []
    for row in rows:
        sid = row["skill_id"]
        desc = desc_map.get(sid, "")
        actual_caps = row.get("actual_caps", "")
        gap_class = row.get("gap_class", "")
        gap_score = float(row.get("gap_text", 0) or 0)
        max_risk = max_risk_level(actual_caps)

        # Count reassurance words per group
        group_counts = {
            grp: count_group(desc, words)
            for grp, words in REASSURANCE_GROUPS.items()
        }
        total_reassurance = sum(v for k, v in group_counts.items() if k != "speed")
        neutral_control = group_counts["speed"]

        # The KEY signal: safety/privacy words when gap is high
        is_true_washing = (
            total_reassurance > 0
            and gap_score >= 0.5
            and max_risk >= 2  # actually has dangerous capability
        )

        # Score: higher = more deceptive (safety words × actual risk × gap)
        washing_score = (total_reassurance * max_risk * gap_score) / max(1, neutral_control + 1)

        results.append({
            "skill_id": sid,
            "description": desc[:200],
            "gap_class": gap_class,
            "gap_score": round(gap_score, 4),
            "actual_caps": actual_caps,
            "max_risk_level": max_risk,
            "safety_words": group_counts["safety"],
            "scope_words": group_counts["scope"],
            "privacy_words": group_counts["privacy"],
            "total_reassurance": total_reassurance,
            "neutral_control": neutral_control,
            "is_true_washing": int(is_true_washing),
            "washing_score": round(washing_score, 4),
            "installs_all_time": row.get("installs_all_time", 0),
        })

    # Sort by washing score
    results.sort(key=lambda r: float(r["washing_score"]), reverse=True)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    # Stats
    total = len(results)
    n_washing = sum(1 for r in results if r["is_true_washing"])
    by_class = Counter(r["gap_class"] for r in results if r["total_reassurance"] > 0)

    # Do reassurance words predict inadequate disclosure?
    with_reassurance = [r for r in results if r["total_reassurance"] > 0]
    without_reassurance = [r for r in results if r["total_reassurance"] == 0]
    bad_with = sum(1 for r in with_reassurance if r["gap_class"] == "inadequate")
    bad_without = sum(1 for r in without_reassurance if r["gap_class"] == "inadequate")

    pct_bad_with = 100 * bad_with / max(len(with_reassurance), 1)
    pct_bad_without = 100 * bad_without / max(len(without_reassurance), 1)

    # Top washing cases
    top_washing = [r for r in results if r["is_true_washing"]][:10]

    lines = [
        "# Reassurance Washing Analysis",
        "",
        f"**Dataset**: {total:,} ClawHub skills with SDG analysis",
        "",
        "## Definition",
        "",
        "> **Reassurance Washing**: A skill that uses safety/privacy/simplicity marketing",
        "> language in its description, but has a high capability disclosure gap",
        "> (gap_score ≥ 0.5) AND contains genuinely dangerous capabilities (max_risk ≥ 2).",
        "",
        "## Key Finding: Is Reassurance Language Inversely Correlated with Disclosure?",
        "",
        "| Group | N | Inadequate Disclosure Rate |",
        "|-------|---|--------------------------|",
        f"| Skills WITH reassurance words | {len(with_reassurance):,} | **{pct_bad_with:.1f}%** |",
        f"| Skills WITHOUT reassurance words | {len(without_reassurance):,} | {pct_bad_without:.1f}% |",
        f"| **Difference** | — | **{pct_bad_with - pct_bad_without:+.1f}pp** |",
        "",
        f"✓ Skills using reassurance language are {'MORE' if pct_bad_with > pct_bad_without else 'LESS'} "
        f"likely to have inadequate disclosure.",
        "",
        "## Reassurance Washing Prevalence",
        "",
        f"- Skills with confirmed reassurance washing: **{n_washing:,}** ({100*n_washing/total:.1f}%)",
        f"- (Has safety/privacy words AND gap ≥ 0.5 AND max_risk ≥ 2)",
        "",
        "## Word Group Breakdown (among washing-positive skills)",
        "",
        "| Word Group | Examples | Count in washing |",
        "|------------|---------|----------------|",
        f"| Safety | safe, secure, harmless | {sum(1 for r in results if r['is_true_washing'] and r['safety_words'])} |",
        f"| Scope minimization | simple, minimal, read-only | {sum(1 for r in results if r['is_true_washing'] and r['scope_words'])} |",
        f"| Privacy | privacy, no data collection | {sum(1 for r in results if r['is_true_washing'] and r['privacy_words'])} |",
        "",
        "## Top 10 Reassurance Washing Cases",
        "",
        "| Skill | Reassurance Words | Actual Caps | Gap |",
        "|-------|------------------|------------|-----|",
    ]

    for r in top_washing:
        lines.append(
            f"| `{r['skill_id']}` | safety={r['safety_words']} scope={r['scope_words']} "
            f"privacy={r['privacy_words']} | {r['actual_caps'][:40]} | {r['gap_score']} |"
        )

    if pct_bad_with > pct_bad_without:
        lines += [
            "",
            "## Paper Implication",
            "",
            f"Skills WITH reassurance language have a **higher** inadequate disclosure rate",
            f"({pct_bad_with:.1f}% vs {pct_bad_without:.1f}%, Δ={pct_bad_with - pct_bad_without:+.1f}pp).",
            "This positive correlation suggests **systematic safety theater** rather than",
            "random negligence — skills that most strongly market themselves as 'safe' are",
            "disproportionately likely to have the largest capability gaps.",
            "This is analogous to 'greenwashing' in environmental marketing.",
        ]
    else:
        lines += [
            "",
            "## Paper Implication",
            "",
            f"Overall, skills WITH reassurance language have a **similar or lower** inadequate",
            f"disclosure rate ({pct_bad_with:.1f}% vs {pct_bad_without:.1f}%, Δ={pct_bad_with - pct_bad_without:+.1f}pp),",
            "suggesting that reassurance words alone are not a reliable indicator of deception.",
            "",
            f"However, the **{n_washing:,} confirmed washing cases** ({100*n_washing/total:.1f}%) are",
            "individually concerning: these skills use safety/privacy language AND have high",
            "capability gaps (≥0.5) AND contain genuinely dangerous capabilities (max_risk ≥ 2).",
            "The threat is concentrated, not population-wide.",
        ]

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"  Reassurance washing: {n_washing:,} ({100*n_washing/total:.1f}%)")
    print(f"  Inadequate rate WITH reassurance words: {pct_bad_with:.1f}%")
    print(f"  Inadequate rate WITHOUT reassurance words: {pct_bad_without:.1f}%")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
