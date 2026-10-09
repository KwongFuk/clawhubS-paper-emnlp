#!/usr/bin/env python3
"""
clawhub/14_ecosystem_analysis.py
----------------------------------
Five-dimension ecosystem-level analysis of problematic ClawHub skills.

  D1: Publisher concentration (Gini coefficient)
  D2: Vertical risk domains (tags × capability heatmap)
  D3: Real-world impact (installs × gap_class)
  D4: Temporal evolution (created_at × disclosure quality)
  D5: Platform defense gap (scan_status vs our findings)

Output:
  clawhub/results/ecosystem_analysis_report.md
"""

from __future__ import annotations
import csv
import json
import sqlite3
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

HERE = Path(__file__).parent
ROOT = HERE.parent.parent.parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"
SDG_CSV = DATA_DIR / "structured_sdg.csv"
DRIFT_CSV = DATA_DIR / "drift_features.csv"
REPORT_PATH = RESULTS_DIR / "ecosystem_analysis_report.md"


def load_csv(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def gini(values: list[float]) -> float:
    if not values: return 0.0
    s = sorted(values)
    n = len(s)
    total = sum(s)
    if total == 0: return 0.0
    cumsum = 0.0
    for i, v in enumerate(s, 1):
        cumsum += v * (2 * i - n - 1)
    return cumsum / (n * total)


def main():
    print("[14_ecosystem] Loading data...")
    db = sqlite3.connect(str(ROOT / "data/experiment1_split/merged.sqlite3"))
    db.row_factory = sqlite3.Row

    skills_meta = {r["skill_id"]: dict(r)
                   for r in db.execute("SELECT * FROM skills").fetchall()}

    sdg_rows = load_csv(SDG_CSV)
    drift_rows = load_csv(DRIFT_CSV)

    # Index by skill_id
    sdg = {r["skill_id"]: r for r in sdg_rows}
    # TDD: aggregate to skill level
    tdd_by_skill: dict[str, list] = defaultdict(list)
    for r in drift_rows:
        tdd_by_skill[r.get("skill_id", "")].append(r)

    all_sids = set(sdg.keys())
    print(f"  SDG skills: {len(sdg)}, TDD skills: {len(tdd_by_skill)}")

    lines = [
        "# Ecosystem-Level Analysis: ClawHub",
        "",
        f"**Dataset**: {len(all_sids):,} skills with SDG analysis",
        "",
    ]

    # ── D1: Publisher Concentration ───────────────────────────────────────────
    owner_total: Counter = Counter()
    owner_bad: Counter = Counter()
    owner_installs: Counter = Counter()
    for sid, row in sdg.items():
        owner = sid.split("/")[0]
        owner_total[owner] += 1
        if row.get("gap_class") == "inadequate":
            owner_bad[owner] += 1
        meta = skills_meta.get(sid, {})
        owner_installs[owner] += int(meta.get("installs_all_time") or 0)

    bad_counts = [owner_bad.get(o, 0) for o in owner_total]
    gini_bad = gini(bad_counts)
    top10_owners = sorted(owner_bad.items(), key=lambda x: -x[1])[:10]
    top10_pct = sum(v for _, v in top10_owners) / max(sum(owner_bad.values()), 1)

    lines += [
        "## D1: Publisher Concentration",
        "",
        f"- Total publishers (owners): **{len(owner_total):,}**",
        f"- Gini coefficient of inadequate skill distribution: **{gini_bad:.3f}**",
        f"  (0=perfectly equal, 1=all from one publisher)",
        f"- Top 10 publishers account for **{100*top10_pct:.1f}%** of inadequate skills",
        "",
        "| Owner | Inadequate Skills | Total Skills |",
        "|-------|-------------------|--------------|",
    ]
    for owner, cnt in top10_owners:
        lines.append(f"| `{owner}` | {cnt} | {owner_total[owner]} |")

    interp = "Concentrated" if gini_bad > 0.6 else "Distributed"
    lines += [
        "",
        f"> **{interp}** distribution: {'a few publishers' if gini_bad > 0.6 else 'many publishers'} "
        f"account for most disclosure gaps.",
        "",
    ]

    # ── D2: Vertical Risk Domains ─────────────────────────────────────────────
    tag_inadequate: Counter = Counter()
    tag_total: Counter = Counter()
    for sid, row in sdg.items():
        meta = skills_meta.get(sid, {})
        tags_raw = meta.get("tags") or "[]"
        try:
            tags = json.loads(tags_raw)
        except Exception:
            tags = []
        if not tags:
            tags = ["untagged"]
        for tag in tags[:3]:
            tag_total[tag] += 1
            if row.get("gap_class") == "inadequate":
                tag_inadequate[tag] += 1

    # Top tags by inadequate rate (min 5 skills)
    tag_rates = {t: tag_inadequate[t] / tag_total[t]
                 for t in tag_total if tag_total[t] >= 5}
    top_tags = sorted(tag_rates.items(), key=lambda x: -x[1])[:15]

    lines += [
        "## D2: Vertical Risk Domains (Tag Analysis)",
        "",
        "| Tag/Domain | Skills | Inadequate | Rate |",
        "|-----------|--------|------------|------|",
    ]
    for tag, rate in top_tags:
        n = tag_total[tag]
        bad = tag_inadequate[tag]
        lines.append(f"| `{tag}` | {n} | {bad} | {100*rate:.0f}% |")
    lines.append("")

    # capability family by domain (top 5 tags)
    lines += [
        "> Higher inadequate rate in domain = higher structural risk in that vertical.",
        "",
    ]

    # ── D3: Real-World Impact ─────────────────────────────────────────────────
    total_installs_at_risk = 0
    hi_risk = []
    for sid, row in sdg.items():
        meta = skills_meta.get(sid, {})
        installs = int(meta.get("installs_all_time") or 0)
        if row.get("gap_class") == "inadequate" and installs > 0:
            total_installs_at_risk += installs
            hi_risk.append((sid, installs, row.get("actual_caps", ""), row.get("gap_text", 0)))

    hi_risk.sort(key=lambda x: -x[1])
    total_installs_all = sum(int(skills_meta.get(s, {}).get("installs_all_time") or 0)
                              for s in sdg)

    lines += [
        "## D3: Real-World Impact Estimation",
        "",
        f"- Total installs across analyzed skills: **{total_installs_all:,}**",
        f"- Installs on **Inadequate** disclosure skills: "
        f"**{total_installs_at_risk:,}** ({100*total_installs_at_risk/max(total_installs_all,1):.1f}%)",
        "",
        "**Top 10 Highest-Install Skills with Inadequate Disclosure:**",
        "",
        "| Skill | Installs | Undisclosed Capabilities | Gap Score |",
        "|-------|----------|--------------------------|-----------|",
    ]
    for sid, insts, caps, gap in hi_risk[:10]:
        lines.append(f"| `{sid}` | {insts:,} | {caps[:60]} | {float(gap):.2f} |")
    lines.append("")

    # ── D4: Temporal Evolution ────────────────────────────────────────────────
    year_total: Counter = Counter()
    year_bad: Counter = Counter()
    for sid, row in sdg.items():
        meta = skills_meta.get(sid, {})
        created = meta.get("created_at", "") or ""
        try:
            year = datetime.fromisoformat(created.replace("Z", "+00:00")).year
        except Exception:
            year = 0
        if year >= 2020:
            year_total[year] += 1
            if row.get("gap_class") == "inadequate":
                year_bad[year] += 1

    lines += [
        "## D4: Temporal Evolution",
        "",
        "| Year | Skills | Inadequate | Rate |",
        "|------|--------|------------|------|",
    ]
    for year in sorted(year_total.keys()):
        n = year_total[year]
        bad = year_bad.get(year, 0)
        lines.append(f"| {year} | {n} | {bad} | {100*bad/max(n,1):.0f}% |")

    lines += [
        "",
        "> If inadequate rate **decreases over time** → platform/authors improving.",
        "> If **stable or increasing** → structural problem, not improving.",
        "",
    ]

    # ── D5: Platform Defense Gap ──────────────────────────────────────────────
    scan_by_class: dict[str, Counter] = defaultdict(Counter)
    for sid, row in sdg.items():
        meta = skills_meta.get(sid, {})
        scan = meta.get("scan_status") or "unknown"
        gap_c = row.get("gap_class", "unknown")
        scan_by_class[gap_c][scan] += 1

    # Skills marked scan_status=clean but inadequate disclosure
    clean_but_bad = scan_by_class.get("inadequate", Counter()).get("clean", 0) + \
                    scan_by_class.get("inadequate", Counter()).get("passed", 0)
    total_bad = sum(scan_by_class.get("inadequate", Counter()).values())

    # TDD candidates vs scan
    tdd_scan: Counter = Counter()
    tdd_candidate_sids = set()
    for sid, pairs in tdd_by_skill.items():
        for p in pairs:
            ces = float(p.get("ces_drift", 0) or 0)
            sds = float(p.get("sds", 1) or 1)
            if ces >= 0.1 and sds <= 0.35:
                tdd_candidate_sids.add(sid)
                break
    for sid in tdd_candidate_sids:
        meta = skills_meta.get(sid, {})
        scan = meta.get("scan_status") or "unknown"
        tdd_scan[scan] += 1

    lines += [
        "## D5: Platform Defense Gap",
        "",
        "### Scan Status of Static SDG-Inadequate Skills",
        "",
        "| Scan Status | Among Inadequate Skills |",
        "|-------------|------------------------|",
    ]
    for scan, cnt in sorted(scan_by_class.get("inadequate", Counter()).items(), key=lambda x: -x[1]):
        lines.append(f"| `{scan}` | {cnt:,} |")

    lines += [
        "",
        f"- Skills with **inadequate disclosure** but scan_status=clean/passed: **{clean_but_bad:,}**",
        f"  ({100*clean_but_bad/max(total_bad,1):.1f}% of inadequate skills passed platform scan)",
        "",
        "### Scan Status of TDD Candidates",
        "",
        "| Scan Status | TDD Candidates |",
        "|-------------|----------------|",
    ]
    for scan, cnt in sorted(tdd_scan.items(), key=lambda x: -x[1]):
        lines.append(f"| `{scan}` | {cnt:,} |")

    lines += [
        "",
        "> Platform scan detects malicious code but **not disclosure gaps**.",
        "> A skill can pass all security scans and still have severe capability opacity.",
        "",
        "## Summary of Ecosystem Findings",
        "",
        "| Dimension | Key Finding |",
        "|-----------|-------------|",
        f"| Publisher | Gini={gini_bad:.2f}: {'concentrated in few publishers' if gini_bad>0.5 else 'distributed across ecosystem'} |",
        f"| Domain | High-risk verticals identified (see tag table) |",
        f"| Impact | {total_installs_at_risk:,} real-world installs on inadequate-disclosure skills |",
        f"| Time | {'Improving' if (year_bad.get(2025,0)/max(year_total.get(2025,1),1)) < (year_bad.get(2023,0)/max(year_total.get(2023,1),1)) else 'Not improving'} over observed period |",
        f"| Defense | {100*clean_but_bad/max(total_bad,1):.0f}% of inadequate skills pass platform scan |",
    ]

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"  Gini: {gini_bad:.3f}")
    print(f"  Total installs at risk: {total_installs_at_risk:,}")
    print(f"  Platform defense gap: {clean_but_bad:,} clean but inadequate")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
