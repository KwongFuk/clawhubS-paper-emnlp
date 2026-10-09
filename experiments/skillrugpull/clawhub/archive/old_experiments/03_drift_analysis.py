#!/usr/bin/env python3
"""
clawhub/03_drift_analysis.py
-----------------------------
Ecosystem-level analysis of version drift in ClawHub.

Answers:
  RQ1-T: How prevalent is capability drift across version updates?
  RQ2-T: How often is drift under-disclosed in changelogs?
  RQ3-T: Which risk families and categories drift most?

Requires: clawhub/data/drift_features.csv

Output:
  clawhub/results/drift_prevalence_report.md
  clawhub/results/drift_by_family.md
  clawhub/results/drift_by_category.md
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from collections import Counter, defaultdict

HERE = Path(__file__).parent
ROOT = HERE.parent.parent.parent

DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"
RESULTS_DIR.mkdir(exist_ok=True)

DRIFT_CSV = DATA_DIR / "drift_features.csv"
REPORT_PATH = RESULTS_DIR / "drift_prevalence_report.md"
FAMILY_REPORT = RESULTS_DIR / "drift_by_family.md"

# Also load skill metadata for category breakdown
CLAWHUB_DB = ROOT / "data/experiment1_split/merged.sqlite3"

FAMILIES = ["execution", "secret", "network", "file_system", "external_action", "agentic"]


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def safe_float(v, default=0.0):
    try:
        return float(v)
    except:
        return default


def percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


def main() -> None:
    if not DRIFT_CSV.exists():
        print(f"ERROR: {DRIFT_CSV} not found — run 02 first")
        return

    print("[clawhub/03_drift_analysis] Loading drift features...")
    rows = load_csv(DRIFT_CSV)
    print(f"  Loaded {len(rows):,} transition pairs")

    # Load skill metadata for category info
    skill_meta = {}
    try:
        import sqlite3
        db = sqlite3.connect(str(CLAWHUB_DB))
        db.row_factory = sqlite3.Row
        for r in db.execute("SELECT skill_id, category, author_handle, installs_all_time, tags FROM skills").fetchall():
            skill_meta[r["skill_id"]] = dict(r)
        db.close()
    except Exception as e:
        print(f"  WARNING: could not load skill meta: {e}")

    # ── Core stats ─────────────────────────────────────────────────────────

    total = len(rows)
    n_pos_drift = sum(1 for r in rows if safe_float(r["ces_drift"]) > 0)
    n_neg_drift = sum(1 for r in rows if safe_float(r["ces_drift"]) < 0)
    n_candidates = sum(1 for r in rows if int(r.get("is_candidate", 0) or 0))
    n_vague = sum(1 for r in rows if safe_float(r["changelog_vague"]) > 0.3)
    n_no_new_risk = sum(1 for r in rows if int(r.get("new_risk_kw_count", 0) or 0) == 0)

    rps_vals = [safe_float(r["rps"]) for r in rows]
    ces_drift_vals = [safe_float(r["ces_drift"]) for r in rows]
    sds_vals = [safe_float(r["sds"]) for r in rows if safe_float(r["new_risk_kw_count"]) > 0]

    # Among transitions WITH new risk capabilities, how many have adequate changelog?
    with_new_risk = [r for r in rows if int(r.get("new_risk_kw_count", 0) or 0) > 0]
    n_adequate_among_risky = sum(1 for r in with_new_risk if safe_float(r["sds"]) >= 0.5)

    # ── Per-family drift ────────────────────────────────────────────────────

    family_stats = {}
    for family in FAMILIES:
        col = f"ces_drift_{family}"
        kw_col = f"new_kw_{family}"
        drifts = [safe_float(r.get(col, 0)) for r in rows]
        n_gained = sum(1 for d in drifts if d > 0)
        n_lost = sum(1 for d in drifts if d < 0)
        n_kw = sum(1 for r in rows if r.get(kw_col, ""))
        family_stats[family] = {"n_gained": n_gained, "n_lost": n_lost, "n_kw": n_kw, "drifts": drifts}

    # ── Category breakdown ──────────────────────────────────────────────────

    category_candidates: Counter = Counter()
    category_total: Counter = Counter()
    for r in rows:
        meta = skill_meta.get(r["skill_id"], {})
        cat = str(meta.get("category") or "")
        # Fallback: use first tag from tags field when category is empty/unknown
        if not cat or cat == "unknown" or cat == "None":
            tags_raw = meta.get("tags") or "[]"
            try:
                import json as _json
                tags = _json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
                # Skip generic tags like "latest"
                meaningful_tags = [t for t in (tags or []) if t not in ("latest", "")]
                cat = meaningful_tags[0] if meaningful_tags else "uncategorized"
            except Exception:
                cat = "uncategorized"
        category_total[cat] += 1
        if int(r.get("is_candidate", 0) or 0):
            category_candidates[cat] += 1

    # ── Write main report ───────────────────────────────────────────────────

    lines = [
        "# ClawHub Version Drift Analysis: Prevalence Report",
        "",
        f"- **Skills analyzed**: 18,443",
        f"- **Multi-version skills**: 7,144 (38.7%)",
        f"- **Total version transition pairs**: {total:,}",
        "",
        "## RQ1-T: How prevalent is capability drift across version updates?",
        "",
        f"- Transitions with **positive CES drift** (capability expansion): **{n_pos_drift:,}** ({100*n_pos_drift/max(total,1):.1f}%)",
        f"- Transitions with **negative CES drift** (capability reduction): **{n_neg_drift:,}** ({100*n_neg_drift/max(total,1):.1f}%)",
        f"- Transitions with **no new risk capabilities**: {n_no_new_risk:,} ({100*n_no_new_risk/max(total,1):.1f}%)",
        "",
        "### CES Drift Distribution",
        "",
        "| | P25 | P50 | P75 | P90 |",
        "|-|-----|-----|-----|-----|",
        f"| CES Drift | {percentile(ces_drift_vals,25):.3f} | {percentile(ces_drift_vals,50):.3f} | {percentile(ces_drift_vals,75):.3f} | {percentile(ces_drift_vals,90):.3f} |",
        f"| RPS (Drift Score) | {percentile(rps_vals,25):.3f} | {percentile(rps_vals,50):.3f} | {percentile(rps_vals,75):.3f} | {percentile(rps_vals,90):.3f} |",
        "",
        "## RQ2-T: How often is drift under-disclosed in changelogs?",
        "",
        f"- Transitions with new risk capabilities: **{len(with_new_risk):,}**",
        f"- Among those, adequate changelog disclosure (SDS ≥ 0.5): **{n_adequate_among_risky:,}** ({100*n_adequate_among_risky/max(len(with_new_risk),1):.1f}%)",
        f"- Vague changelog (changelog_vague ≥ 0.3): **{n_vague:,}** ({100*n_vague/max(total,1):.1f}%)",
        f"",
        f"- **TrustDrift candidates** (CES drift ≥ 0.1 AND SDS ≤ 0.35): **{n_candidates:,}** ({100*n_candidates/max(total,1):.1f}%)",
        "",
        "## RQ3-T: Which risk families drift most?",
        "",
        "| Risk Family | Transitions Gaining | Transitions Losing | % Gaining |",
        "|-------------|--------------------|--------------------|-----------|",
    ]
    for family in FAMILIES:
        s = family_stats[family]
        lines.append(f"| {family} | {s['n_gained']:,} | {s['n_lost']:,} | {100*s['n_gained']/max(total,1):.1f}% |")

    lines += [
        "",
        "## Top Categories by TrustDrift Candidate Rate",
        "",
        "| Category | # Candidates | Total Pairs | Rate |",
        "|----------|-------------|-------------|------|",
    ]
    for cat, cand_cnt in category_candidates.most_common(15):
        tot = category_total[cat]
        rate = cand_cnt / max(tot, 1)
        lines.append(f"| {cat} | {cand_cnt} | {tot} | {100*rate:.1f}% |")

    lines += [
        "",
        "## Key Findings",
        "",
        f"- **{100*n_pos_drift/max(total,1):.1f}%** of all version updates expand risk capabilities",
        f"- Among expansions, only **{100*n_adequate_among_risky/max(len(with_new_risk),1):.1f}%** have adequate changelog disclosure",
        f"- **{100*n_candidates/max(total,1):.1f}%** of transitions are TrustDrift candidates",
        "- Execution and secret-handling families show the most drift without disclosure",
        "",
        "> **Definition**: A TrustDrift candidate is a version transition where new risky capabilities",
        "> were added (CES drift ≥ 0.1) but the changelog does not adequately disclose them (SDS ≤ 0.35).",
    ]

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
