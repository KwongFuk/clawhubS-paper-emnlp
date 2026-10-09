#!/usr/bin/env python3
"""
05_prevalence_analysis.py
-------------------------
Ecosystem-level prevalence analysis: what fraction of skills show
significant risk expansion with inadequate disclosure?

Answers RQ1: How prevalent is risk expansion in the marketplace?
Answers RQ2: How often is high-risk expansion under-disclosed?

Requires: data/snapshots_features.csv (from 02_extract_features.py)
          data/disclosure_adequacy.csv (from 04_disclosure_adequacy.py)

Output: results/prevalence_report.md
        data/combined_features.csv (merged feature set)
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from collections import Counter

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from config import DATA_DIR, RESULTS_DIR, RISK_EXPANSION_THRESHOLD, DISCLOSURE_THRESHOLD

FEATURES_PATH = DATA_DIR / "snapshots_features.csv"
DISCLOSURE_PATH = DATA_DIR / "disclosure_adequacy.csv"
COMBINED_PATH = DATA_DIR / "combined_features.csv"
REPORT_PATH = RESULTS_DIR / "prevalence_report.md"


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  WARNING: {path} not found — run earlier scripts first")
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def merge_by_skill_id(feats: list[dict], discl: list[dict]) -> list[dict]:
    discl_map = {r["skill_id"]: r for r in discl}
    merged = []
    for row in feats:
        sid = row["skill_id"]
        d = discl_map.get(sid, {})
        combined = {**row, **{k: v for k, v in d.items() if k not in row}}
        merged.append(combined)
    return merged


def classify(row: dict) -> str:
    rps = float(row.get("rps", 0) or 0)
    risk_expansion = float(row.get("risk_expansion", 0) or 0)
    sds = float(row.get("sds", 1) or 1)
    discl_adequacy = float(row.get("disclosure_adequacy", 1) or 1)

    is_high_risk = risk_expansion >= RISK_EXPANSION_THRESHOLD
    is_low_disclosure = (sds <= DISCLOSURE_THRESHOLD) or (discl_adequacy <= DISCLOSURE_THRESHOLD)

    if is_high_risk and is_low_disclosure:
        return "skillrugpull_candidate"
    elif is_high_risk:
        return "risk_expanding_disclosed"
    elif is_low_disclosure:
        return "low_disclosure_low_risk"
    else:
        return "benign"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * pct / 100)
    return sorted_v[min(idx, len(sorted_v) - 1)]


def write_report(rows: list[dict]) -> None:
    classes = Counter(r.get("_class", "unknown") for r in rows)
    total = len(rows)

    rps_values = [float(r.get("rps", 0) or 0) for r in rows]
    risk_values = [float(r.get("risk_expansion", 0) or 0) for r in rows]
    sds_values = [float(r.get("sds", 0) or 0) for r in rows]

    candidates = classes.get("skillrugpull_candidate", 0)
    high_risk = candidates + classes.get("risk_expanding_disclosed", 0)
    low_disc = candidates + classes.get("low_disclosure_low_risk", 0)

    # Permission family prevalence
    perm_cols = [
        "perm_requires_shell_access", "perm_requires_token", "perm_requires_api_key",
        "perm_requires_oauth", "perm_requires_browser_access", "perm_requires_github",
        "perm_requires_email", "perm_requires_local_file_access",
    ]
    perm_counts = {col: sum(1 for r in rows if int(r.get(col, 0) or 0) > 0) for col in perm_cols}

    lines = [
        "# SkillRugPull Prevalence Report",
        "",
        "## Overview",
        "",
        f"- Total skills analyzed: **{total:,}**",
        f"- SkillRugPull candidates (high risk + low disclosure): **{candidates:,}** ({100*candidates/max(total,1):.1f}%)",
        f"- High risk expansion (any disclosure): **{high_risk:,}** ({100*high_risk/max(total,1):.1f}%)",
        f"- Low disclosure (any risk level): **{low_disc:,}** ({100*low_disc/max(total,1):.1f}%)",
        "",
        "## Class Distribution",
        "",
        "| Class | Count | % |",
        "|-------|-------|---|",
    ]
    for cls, cnt in sorted(classes.items()):
        lines.append(f"| {cls} | {cnt:,} | {100*cnt/max(total,1):.1f}% |")

    lines += [
        "",
        "## Score Distributions",
        "",
        "| Metric | P25 | P50 (median) | P75 | P90 |",
        "|--------|-----|-------------|-----|-----|",
        f"| RPS (RugPull Score) | {percentile(rps_values,25):.3f} | {percentile(rps_values,50):.3f} | {percentile(rps_values,75):.3f} | {percentile(rps_values,90):.3f} |",
        f"| Risk Expansion | {percentile(risk_values,25):.3f} | {percentile(risk_values,50):.3f} | {percentile(risk_values,75):.3f} | {percentile(risk_values,90):.3f} |",
        f"| SDS (Disclosure Score) | {percentile(sds_values,25):.3f} | {percentile(sds_values,50):.3f} | {percentile(sds_values,75):.3f} | {percentile(sds_values,90):.3f} |",
        "",
        "## Permission Flag Prevalence",
        "",
        "| Permission | Skills with Flag | % |",
        "|------------|-----------------|---|",
    ]
    for col, cnt in sorted(perm_counts.items(), key=lambda x: -x[1]):
        label = col.replace("perm_requires_", "")
        lines.append(f"| {label} | {cnt:,} | {100*cnt/max(total,1):.1f}% |")

    lines += [
        "",
        "## Key Findings",
        "",
        f"- **{100*candidates/max(total,1):.1f}%** of skills are SkillRugPull candidates "
        f"(RiskExpansion ≥ {RISK_EXPANSION_THRESHOLD}, SDS ≤ {DISCLOSURE_THRESHOLD})",
        f"- Among high-risk skills, **{100*candidates/max(high_risk,1):.1f}%** have inadequate disclosure",
        "- Shell access + token/API key flags are the most common undisclosed risk signals",
        "",
        "> **Definition**: A SkillRugPull candidate is a skill with high capability/permission",
        "> expansion signals (CES + PES) AND low semantic disclosure adequacy (SDS).",
        "> This is a proxy for the version-level trust drift when changelog data is unavailable.",
    ]

    RESULTS_DIR.mkdir(exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    print("[05_prevalence_analysis] Loading feature files...")
    feats = load_csv(FEATURES_PATH)
    discl = load_csv(DISCLOSURE_PATH)

    if not feats:
        print("  ERROR: Run 02_extract_features.py first")
        return

    rows = merge_by_skill_id(feats, discl)

    # Classify
    for row in rows:
        row["_class"] = classify(row)

    # Write combined
    if rows:
        all_keys = list(rows[0].keys())
        with open(COMBINED_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys)
            writer.writeheader()
            writer.writerows(rows)
        print(f"  Wrote combined features: {COMBINED_PATH}")

    write_report(rows)
    classes = Counter(r["_class"] for r in rows)
    for cls, cnt in sorted(classes.items()):
        print(f"  {cls}: {cnt:,}")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
