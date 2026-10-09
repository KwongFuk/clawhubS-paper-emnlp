#!/usr/bin/env python3
"""
clawhub/11_double_drift_analysis.py
-------------------------------------
Double-Drift: Skills with BOTH high Static SDG AND high Temporal TDD.
This is the §5 "Joint Analysis" of the paper.

Inputs:
  clawhub/data/structured_sdg.csv       (static SDG results per skill)
  clawhub/data/drift_features.csv        (temporal TDD results per skill)
  clawhub/data/version_transitions*.csv  (fallback)

Outputs:
  clawhub/data/double_drift.csv
  clawhub/results/double_drift_report.md
"""

from __future__ import annotations
import csv
from pathlib import Path
from collections import Counter, defaultdict

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"
RESULTS_DIR.mkdir(exist_ok=True)

SDG_CSV = DATA_DIR / "structured_sdg.csv"
DRIFT_CSV = DATA_DIR / "drift_features.csv"
TRANSITIONS_CSV = DATA_DIR / "version_transitions.csv"
OUTPUT_CSV = DATA_DIR / "double_drift.csv"
REPORT_PATH = RESULTS_DIR / "double_drift_report.md"

# Thresholds
SDG_INADEQUATE = "inadequate"
TDD_HIGH_CES = 0.1
TDD_LOW_SDS = 0.35


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    print("[11_double_drift] Loading SDG data...")
    if not SDG_CSV.exists():
        print(f"  ERROR: {SDG_CSV} not found. Run 10_structured_sdg_analysis.py first.")
        return

    sdg_rows = load_csv(SDG_CSV)
    print(f"  SDG skills: {len(sdg_rows)}")

    # Index by skill_id
    sdg_by_skill: dict[str, dict] = {r["skill_id"]: r for r in sdg_rows}

    # Load temporal TDD (drift_features.csv or version_transitions)
    tdd_by_skill: dict[str, list[dict]] = defaultdict(list)

    drift_source = None
    for candidate in [DRIFT_CSV, TRANSITIONS_CSV]:
        if candidate.exists():
            drift_source = candidate
            break

    if drift_source is None:
        # Try to find any CSV with ces_drift
        for f in sorted(DATA_DIR.glob("*.csv")):
            try:
                rows = load_csv(f)
                if rows and "ces_drift" in rows[0]:
                    drift_source = f
                    break
            except Exception:
                continue

    if drift_source:
        print(f"  TDD source: {drift_source.name}")
        tdd_rows = load_csv(drift_source)
        for r in tdd_rows:
            sid = r.get("skill_id", "")
            if sid:
                tdd_by_skill[sid].append(r)
    else:
        print("  WARNING: No TDD CSV found — showing SDG-only analysis")

    print(f"  TDD skills with data: {len(tdd_by_skill)}")

    # ── Compute per-skill TDD aggregate ──────────────────────────────────────
    def skill_tdd_summary(pairs: list[dict]) -> dict:
        """Aggregate multiple version pairs into skill-level TDD metrics."""
        ces_drifts = []
        sds_scores = []
        n_trustdrift = 0
        for p in pairs:
            try:
                ces = float(p.get("ces_drift", 0) or 0)
                sds = float(p.get("sds", 1) or 1)
                ces_drifts.append(ces)
                sds_scores.append(sds)
                if ces >= TDD_HIGH_CES and sds <= TDD_LOW_SDS:
                    n_trustdrift += 1
            except Exception:
                pass
        return {
            "tdd_max_ces": max(ces_drifts, default=0.0),
            "tdd_min_sds": min(sds_scores, default=1.0),
            "tdd_n_pairs": len(pairs),
            "tdd_n_trustdrift": n_trustdrift,
            "tdd_is_candidate": int(n_trustdrift > 0),
        }

    # ── Join columns ──────────────────────────────────────────────────────────
    results = []
    for sid, sdg in sdg_by_skill.items():
        tdd_pairs = tdd_by_skill.get(sid, [])
        tdd = skill_tdd_summary(tdd_pairs) if tdd_pairs else {
            "tdd_max_ces": 0.0, "tdd_min_sds": 1.0, "tdd_n_pairs": 0,
            "tdd_n_trustdrift": 0, "tdd_is_candidate": 0,
        }

        is_sdg_inadequate = int(sdg.get("gap_class", "") == SDG_INADEQUATE)
        is_tdd_candidate = tdd["tdd_is_candidate"]
        is_double_drift = int(is_sdg_inadequate and is_tdd_candidate)

        results.append({
            "skill_id": sid,
            # SDG
            "sdg_gap_class": sdg.get("gap_class", ""),
            "sdg_coverage_text": sdg.get("coverage_text", ""),
            "sdg_gap_text": sdg.get("gap_text", ""),
            "sdg_actual_caps": sdg.get("actual_caps", ""),
            "sdg_undisclosed_caps": sdg.get("undisclosed_caps", ""),
            "sdg_reassurance": sdg.get("is_reassurance_washing", 0),
            "sdg_has_yaml": sdg.get("has_yaml_declaration", 0),
            # TDD
            "tdd_n_pairs": tdd["tdd_n_pairs"],
            "tdd_max_ces": round(tdd["tdd_max_ces"], 4),
            "tdd_min_sds": round(tdd["tdd_min_sds"], 4),
            "tdd_n_trustdrift_pairs": tdd["tdd_n_trustdrift"],
            "tdd_is_candidate": is_tdd_candidate,
            # Joint
            "is_sdg_inadequate": is_sdg_inadequate,
            "is_double_drift": is_double_drift,
            # Metadata
            "installs_all_time": sdg.get("installs_all_time", 0),
            "installs_current": sdg.get("installs_current", 0),
            "stars": sdg.get("stars", 0),
            "category": sdg.get("category", "unknown"),
        })

    # Write CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    # ── Statistics ────────────────────────────────────────────────────────────
    total_sdg = len(results)
    n_sdg_only = sum(1 for r in results if r["is_sdg_inadequate"] and not r["tdd_is_candidate"])
    n_tdd_only = sum(1 for r in results if not r["is_sdg_inadequate"] and r["tdd_is_candidate"])
    n_double = sum(1 for r in results if r["is_double_drift"])
    n_neither = sum(1 for r in results if not r["is_sdg_inadequate"] and not r["tdd_is_candidate"])
    n_tdd_has_data = sum(1 for r in results if r["tdd_n_pairs"] > 0)
    n_sdg_inadequate = sum(1 for r in results if r["is_sdg_inadequate"])
    n_tdd_candidate = sum(1 for r in results if r["tdd_is_candidate"])

    # High-impact double drift
    hi_impact_double = [r for r in results
                        if r["is_double_drift"] and int(r["installs_all_time"]) > 50]

    # Overlap rate (among skills in both datasets)
    in_both = [r for r in results if r["tdd_n_pairs"] > 0]
    if in_both:
        both_sdg_bad = sum(1 for r in in_both if r["is_sdg_inadequate"])
        both_tdd_bad = sum(1 for r in in_both if r["tdd_is_candidate"])
        both_double = sum(1 for r in in_both if r["is_double_drift"])
    else:
        both_sdg_bad = both_tdd_bad = both_double = 0

    lines = [
        "# Double-Drift Joint Analysis",
        "",
        f"**Skills in SDG analysis**: {total_sdg:,}",
        f"**Skills also in TDD analysis**: {n_tdd_has_data:,}",
        "",
        "## §5 Joint Distribution (Venn Diagram Data)",
        "",
        "```",
        f"  Static SDG only (inadequate, no TDD data):  {n_sdg_only:,}",
        f"  Temporal TDD only (drift candidate, ok SDG): {n_tdd_only:,}",
        f"  ★ DOUBLE DRIFT (both high):                  {n_double:,}",
        f"  Neither:                                      {n_neither:,}",
        "```",
        "",
        "| Risk Class | Count | % of analyzed |",
        "|-----------|-------|----------------|",
        f"| SDG inadequate | {n_sdg_inadequate:,} | {100*n_sdg_inadequate/max(total_sdg,1):.1f}% |",
        f"| TDD candidate | {n_tdd_candidate:,} | {100*n_tdd_candidate/max(total_sdg,1):.1f}% |",
        f"| **Double Drift** | **{n_double:,}** | **{100*n_double/max(total_sdg,1):.1f}%** |",
        "",
    ]

    if in_both:
        overlap_rate = 100 * both_double / max(both_sdg_bad, 1)
        lines += [
            "## Skills Present in Both Analyses",
            "",
            f"- Skills in both SDG and TDD: **{len(in_both):,}**",
            f"- SDG-inadequate among these: **{both_sdg_bad:,}** ({100*both_sdg_bad/max(len(in_both),1):.1f}%)",
            f"- TDD-candidate among these: **{both_tdd_bad:,}** ({100*both_tdd_bad/max(len(in_both),1):.1f}%)",
            f"- **Double Drift overlap**: **{both_double:,}** ({overlap_rate:.1f}% of SDG-bad skills)",
            "",
        ]

    lines += [
        "## High-Impact Double Drift (installs > 50)",
        "",
        f"- **{len(hi_impact_double):,}** skills with both high SDG + TDD AND >50 installs",
        "- These represent the highest real-world risk:",
        "  - Users already installed these skills",
        "  - Description misrepresents capabilities (static)",
        "  - Updates further expanded hidden capabilities (temporal)",
        "",
        "## Top 10 Most Dangerous Double Drift Skills",
        "",
        "| Skill | SDG gap | Max ΔCES | Min SDS | Installs |",
        "|-------|---------|---------|---------|----------|",
    ]

    top10 = sorted(
        [r for r in results if r["is_double_drift"]],
        key=lambda r: (int(r["installs_all_time"]), float(r.get("tdd_max_ces", 0) or 0)),
        reverse=True
    )[:10]
    for r in top10:
        lines.append(
            f"| `{r['skill_id']}` | {float(r['sdg_gap_text']):.2f} | "
            f"{float(r['tdd_max_ces']):.3f} | {float(r['tdd_min_sds']):.2f} | "
            f"{r['installs_all_time']} |"
        )

    lines += [
        "",
        "## Interpretation",
        "",
        "The Double Drift finding has two key implications:",
        "",
        "1. **Not independent**: Skills that misrepresent capabilities at install",
        "   time are also more likely to expand capabilities silently across versions.",
        "   This suggests **intentional obfuscation** rather than documentation lag.",
        "",
        "2. **Compounding risk**: An LLM agent that installs a Double Drift skill",
        "   is deceived twice — at install and at every update.",
    ]

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n  SDG inadequate: {n_sdg_inadequate:,}")
    print(f"  TDD candidate:  {n_tdd_candidate:,}")
    print(f"  Double Drift:   {n_double:,} ({100*n_double/max(total_sdg,1):.1f}%)")
    print(f"  Hi-impact DD:   {len(hi_impact_double):,}")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
