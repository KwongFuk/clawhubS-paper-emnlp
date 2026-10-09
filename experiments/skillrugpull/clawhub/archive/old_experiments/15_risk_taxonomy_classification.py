#!/usr/bin/env python3
"""
clawhub/15_risk_taxonomy_classification.py
-------------------------------------------
Classifies all problematic ClawHub skills into a hierarchical risk taxonomy,
producing counts for each sub-category to generate a taxonomy figure.

Taxonomy structure:
  Capability Non-Disclosure
  ├── [A] Static Disclosure Gap (SDG)
  │   ├── A1: Shell / Code Execution hidden
  │   ├── A2: Credential Access hidden
  │   ├── A3: Network Exfiltration hidden
  │   ├── A4: Autonomous Behavior hidden
  │   └── A5: File System Access hidden
  │
  ├── [B] Temporal Disclosure Drift (TDD)
  │   ├── B1: Silent Capability Injection (T1)
  │   ├── B2: Credential Scope Creep (T2)
  │   ├── B3: Documentation Lag (T3a)
  │   ├── B4: Platform-Triggered Revert (T3b)
  │   └── B5: Confirmed Malicious (T6)
  │
  └── [C] Double Drift
      ├── C1: SDG + TDD both present
      └── C2: High install impact

Output:
  clawhub/results/risk_taxonomy.md  (counts for figure)
  clawhub/results/risk_taxonomy.csv (full per-skill classification)
"""

from __future__ import annotations
import csv
from pathlib import Path
from collections import Counter

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"

SDG_CSV = DATA_DIR / "structured_sdg.csv"
DRIFT_CSV = DATA_DIR / "drift_features.csv"
DOUBLE_CSV = DATA_DIR / "double_drift.csv"
TAXONOMY_REPORT = RESULTS_DIR / "risk_taxonomy.md"
TAXONOMY_CSV = RESULTS_DIR / "risk_taxonomy.csv"


def load_csv(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def has_cap(caps_str: str, cap: str) -> bool:
    return cap in (caps_str or "").split("|")


def any_cap(caps_str: str, caps: list[str]) -> bool:
    present = set((caps_str or "").split("|"))
    return bool(present & set(caps))


def main():
    print("[15_risk_taxonomy] Loading data...")
    sdg_rows = {r["skill_id"]: r for r in load_csv(SDG_CSV)}
    double_rows = {r["skill_id"]: r for r in load_csv(DOUBLE_CSV)}
    drift_rows_raw = load_csv(DRIFT_CSV)

    # Aggregate TDD types per skill from drift_features
    from collections import defaultdict
    tdd_type_by_skill: dict[str, set] = defaultdict(set)
    for r in drift_rows_raw:
        sid = r.get("skill_id", "")
        auto_label = r.get("auto_label", "") or ""
        ces = float(r.get("ces_drift", 0) or 0)
        sds = float(r.get("sds", 1) or 1)
        if ces >= 0.1 and sds <= 0.35:
            # Classify into TDD sub-type
            if "T6" in auto_label:
                tdd_type_by_skill[sid].add("B5_malicious")
            elif "T1" in auto_label:
                tdd_type_by_skill[sid].add("B1_silent_injection")
            elif "T2" in auto_label:
                tdd_type_by_skill[sid].add("B2_cred_creep")
            elif "T3b" in auto_label:
                tdd_type_by_skill[sid].add("B4_platform_revert")
            elif "T3a" in auto_label:
                tdd_type_by_skill[sid].add("B3_doc_lag")
            else:
                tdd_type_by_skill[sid].add("B1_silent_injection")  # default TDD

    print(f"  SDG skills: {len(sdg_rows)}, TDD skills with type: {len(tdd_type_by_skill)}")

    # ── Classify each skill ───────────────────────────────────────────────────
    results = []
    # Track counts
    sdg_by_type: Counter = Counter()
    tdd_by_type: Counter = Counter()
    double_count = 0
    hi_impact = 0

    for sid, sdg in sdg_rows.items():
        gap_class = sdg.get("gap_class", "")
        undisclosed = sdg.get("undisclosed_caps", "")
        dd = double_rows.get(sid, {})
        is_double = dd.get("is_double_drift") == "1"
        installs = int(sdg.get("installs_all_time", 0) or 0)

        # ── SDG sub-classification ─────────────────────────────────────────
        sdg_types = []
        if gap_class == "inadequate":
            if any_cap(undisclosed, ["SHELL_EXEC", "FILE_DELETE", "FILE_WRITE"]):
                sdg_types.append("A1_shell_exec")
                sdg_by_type["A1_shell_exec"] += 1
            if any_cap(undisclosed, ["CRED_READ", "CRED_STORE", "CRED_TRANSMIT"]):
                sdg_types.append("A2_credential")
                sdg_by_type["A2_credential"] += 1
            if any_cap(undisclosed, ["NET_WRITE", "NET_READ"]):
                sdg_types.append("A3_network")
                sdg_by_type["A3_network"] += 1
            if any_cap(undisclosed, ["AUTONOMOUS", "MEMORY_WRITE"]):
                sdg_types.append("A4_autonomous")
                sdg_by_type["A4_autonomous"] += 1
            if any_cap(undisclosed, ["FILE_READ", "FILE_WRITE"]):
                sdg_types.append("A5_filesystem")
                sdg_by_type["A5_filesystem"] += 1
            if not sdg_types:
                sdg_types.append("A_inadequate_other")
                sdg_by_type["A_inadequate_other"] += 1

        # ── TDD sub-classification ─────────────────────────────────────────
        tdd_types = list(tdd_type_by_skill.get(sid, []))
        for t in tdd_types:
            tdd_by_type[t] += 1

        # ── Double Drift ───────────────────────────────────────────────────
        if is_double:
            double_count += 1
            if installs > 50:
                hi_impact += 1

        results.append({
            "skill_id": sid,
            "gap_class": gap_class,
            "sdg_types": "|".join(sdg_types),
            "tdd_types": "|".join(tdd_types),
            "is_double_drift": int(is_double),
            "installs_all_time": installs,
            "undisclosed_caps": undisclosed,
        })

    # Save per-skill CSV
    with open(TAXONOMY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    # ── Build report / taxonomy counts table ──────────────────────────────────
    total_analyzed = len(results)
    n_adequate = sum(1 for r in results if r["gap_class"] in ("adequate", "no_risk"))
    n_sdg_total = sum(1 for r in results if r["gap_class"] == "inadequate")
    n_tdd_total = len(tdd_type_by_skill)
    n_double = double_count

    lines = [
        "# Risk Taxonomy Classification",
        "",
        f"**Total skills analyzed**: {total_analyzed:,}",
        "",
        "## Taxonomy (for Figure)",
        "",
        "```",
        "Capability Non-Disclosure",
        f"├── [A] Static Disclosure Gap (SDG)          {n_sdg_total:,} skills",
        f"│   ├── A1: Shell/Code Execution hidden        {sdg_by_type['A1_shell_exec']:,}",
        f"│   ├── A2: Credential Access hidden           {sdg_by_type['A2_credential']:,}",
        f"│   ├── A3: Network Exfiltration hidden        {sdg_by_type['A3_network']:,}",
        f"│   ├── A4: Autonomous Behavior hidden         {sdg_by_type['A4_autonomous']:,}",
        f"│   └── A5: File System Access hidden          {sdg_by_type['A5_filesystem']:,}",
        "│",
        f"├── [B] Temporal Disclosure Drift (TDD)       {n_tdd_total:,} skills",
        f"│   ├── B1: Silent Capability Injection         {tdd_by_type.get('B1_silent_injection', 0):,}",
        f"│   ├── B2: Credential Scope Creep              {tdd_by_type.get('B2_cred_creep', 0):,}",
        f"│   ├── B3: Documentation Lag                   {tdd_by_type.get('B3_doc_lag', 0):,}",
        f"│   ├── B4: Platform-Triggered Revert           {tdd_by_type.get('B4_platform_revert', 0):,}",
        f"│   └── B5: Confirmed Malicious (T6)            {tdd_by_type.get('B5_malicious', 0):,}",
        "│",
        f"├── [C] Double Drift (SDG + TDD)              {n_double:,} skills",
        f"│   ├── C1: Both SDG + TDD present             {n_double:,}",
        f"│   └── C2: High-install impact (>50)          {hi_impact:,}",
        "│",
        f"└── [D] No Issue (Adequate Disclosure)        {n_adequate:,} skills",
        "```",
        "",
        "## Counts Table (for Paper Figure Data)",
        "",
        "| Category | Sub-type | Count | % of total |",
        "|---------|---------|-------|------------|",
        f"| **A: Static SDG** | Total | {n_sdg_total} | {100*n_sdg_total/total_analyzed:.1f}% |",
        f"| | A1: Shell/Code Execution | {sdg_by_type['A1_shell_exec']} | {100*sdg_by_type['A1_shell_exec']/total_analyzed:.1f}% |",
        f"| | A2: Credential Access | {sdg_by_type['A2_credential']} | {100*sdg_by_type['A2_credential']/total_analyzed:.1f}% |",
        f"| | A3: Network Exfiltration | {sdg_by_type['A3_network']} | {100*sdg_by_type['A3_network']/total_analyzed:.1f}% |",
        f"| | A4: Autonomous Behavior | {sdg_by_type['A4_autonomous']} | {100*sdg_by_type['A4_autonomous']/total_analyzed:.1f}% |",
        f"| | A5: File System | {sdg_by_type['A5_filesystem']} | {100*sdg_by_type['A5_filesystem']/total_analyzed:.1f}% |",
        f"| **B: Temporal TDD** | Total | {n_tdd_total} | {100*n_tdd_total/total_analyzed:.1f}% |",
        f"| | B1: Silent Injection | {tdd_by_type.get('B1_silent_injection', 0)} | {100*tdd_by_type.get('B1_silent_injection',0)/total_analyzed:.1f}% |",
        f"| | B2: Credential Creep | {tdd_by_type.get('B2_cred_creep', 0)} | {100*tdd_by_type.get('B2_cred_creep',0)/total_analyzed:.1f}% |",
        f"| | B3: Documentation Lag | {tdd_by_type.get('B3_doc_lag', 0)} | {100*tdd_by_type.get('B3_doc_lag',0)/total_analyzed:.1f}% |",
        f"| | B4: Platform Revert | {tdd_by_type.get('B4_platform_revert', 0)} | {100*tdd_by_type.get('B4_platform_revert',0)/total_analyzed:.1f}% |",
        f"| | B5: Confirmed Malicious | {tdd_by_type.get('B5_malicious', 0)} | {100*tdd_by_type.get('B5_malicious',0)/total_analyzed:.1f}% |",
        f"| **C: Double Drift** | C1: Both | {n_double} | {100*n_double/total_analyzed:.1f}% |",
        f"| | C2: High-install | {hi_impact} | {100*hi_impact/total_analyzed:.1f}% |",
        f"| **D: Adequate** | No issue | {n_adequate} | {100*n_adequate/total_analyzed:.1f}% |",
    ]

    with open(TAXONOMY_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n  SDG total: {n_sdg_total:,}")
    print(f"    A1 Shell: {sdg_by_type['A1_shell_exec']:,}")
    print(f"    A2 Cred:  {sdg_by_type['A2_credential']:,}")
    print(f"    A3 Net:   {sdg_by_type['A3_network']:,}")
    print(f"    A4 Auto:  {sdg_by_type['A4_autonomous']:,}")
    print(f"  TDD total: {n_tdd_total:,}")
    print(f"    B1: {tdd_by_type.get('B1_silent_injection',0):,}")
    print(f"    B5: {tdd_by_type.get('B5_malicious',0):,}")
    print(f"  Double Drift: {n_double:,}")
    print(f"  Adequate/No-risk: {n_adequate:,}")
    print(f"  Report: {TAXONOMY_REPORT}")


if __name__ == "__main__":
    main()
