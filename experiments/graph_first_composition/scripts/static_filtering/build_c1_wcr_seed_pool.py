#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from clawhub_analyzer.p25r_protocol import source_author  # noqa: E402


IN_CSV = EXPERIMENT_ROOT / "data/analysis_c1_static/c1_cas_wcr_pairs.csv"
OUT_DIR = EXPERIMENT_ROOT / "data/analysis_c1_static"
OUT_CSV = OUT_DIR / "c1_case_seed_pool_wcr.csv"
OUT_SUMMARY = OUT_DIR / "c1_seed_pool_wcr_summary.md"
OUT_AUDIT = OUT_DIR / "c1_selection_wcr_audit.json"

MAX_PER_SOURCE = 2
MAX_PER_AUTHOR = 4
C1_FAMILY_QUOTAS = {
    "booking_create": 12,
    "order_submit": 8,
    "workspace_reseller": 12,
    "device_control": 18,
}


def candidate_priority(row: dict[str, str]) -> tuple[int, float, float, float, str, str]:
    tier_rank = {"W3_high": 0, "W2_medium": 1, "W1_low": 2, "W0_none": 3}.get(row["when_reachability_tier"], 9)
    return (
        tier_rank,
        -float(row["wcr_score"]),
        -float(row["composition_support_score"]),
        -float(row["singleton_safety_plausibility"]),
        row["sink_skill_id"],
        row["source_skill_id"],
    )


def row_identity(row: dict[str, str]) -> tuple[str, str, str]:
    return (row["source_skill_id"], row["sink_skill_id"], row["composition_trigger_type"])


def main() -> int:
    if not IN_CSV.exists():
        raise FileNotFoundError(f"missing WCR file: {IN_CSV}")

    with IN_CSV.open(encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["c1_wcr_dynamic_candidate"] == "True" and row["runnable_family"]
        ]

    rows_by_family: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        rows_by_family[row["runnable_family"]].append(row)

    selected: list[dict[str, str]] = []
    selected_ids: set[tuple[str, str, str]] = set()
    per_source: Counter[str] = Counter()
    per_author: Counter[str] = Counter()
    audit: dict[str, object] = {"requested": C1_FAMILY_QUOTAS, "per_family": {}, "rejections": Counter(), "shortfalls": {}}

    for family, quota in C1_FAMILY_QUOTAS.items():
        picked = 0
        family_rejections: Counter[str] = Counter()
        for row in sorted(rows_by_family.get(family, []), key=candidate_priority):
            if picked >= quota:
                break
            key = row_identity(row)
            if key in selected_ids:
                continue
            source_id = row["source_skill_id"]
            author = source_author(source_id)
            if per_source[source_id] >= MAX_PER_SOURCE:
                family_rejections["source_cap"] += 1
                audit["rejections"]["source_cap"] += 1
                continue
            if per_author[author] >= MAX_PER_AUTHOR:
                family_rejections["author_cap"] += 1
                audit["rejections"]["author_cap"] += 1
                continue
            selected.append(row)
            selected_ids.add(key)
            per_source[source_id] += 1
            per_author[author] += 1
            picked += 1

        audit["per_family"][family] = {"selected": picked, "rejections": dict(family_rejections)}
        if picked < quota:
            audit["shortfalls"][family] = quota - picked

    audit["rejections"] = dict(audit["rejections"])

    selected.sort(key=candidate_priority)
    fieldnames = list(selected[0].keys()) if selected else []
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(selected)

    by_family = Counter(row["runnable_family"] for row in selected)
    by_tier = Counter(row["when_reachability_tier"] for row in selected)

    lines = [
        "# C1 WCR Seed Pool",
        "",
        "> Source: `c1_cas_wcr_pairs.csv` filtered to WCR-passing runnable families.",
        "",
        "## Overview",
        "",
        f"- selected rows: `{len(selected)}`",
        f"- max per source: `{MAX_PER_SOURCE}`",
        f"- max per source author: `{MAX_PER_AUTHOR}`",
        f"- family quotas: `{json.dumps(C1_FAMILY_QUOTAS, ensure_ascii=False)}`",
        f"- shortfalls: `{json.dumps(audit['shortfalls'], ensure_ascii=False)}`",
        "",
        "## By Family",
        "",
        "| Family | Selected |",
        "| --- | ---: |",
    ]
    for family, count in sorted(by_family.items()):
        lines.append(f"| `{family}` | {count} |")
    lines.extend(
        [
            "",
            "## By WCR Tier",
            "",
            "| Tier | Selected |",
            "| --- | ---: |",
        ]
    )
    for tier, count in sorted(by_tier.items()):
        lines.append(f"| `{tier}` | {count} |")

    OUT_SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        json.dumps(
            {
                "seed_rows": len(selected),
                "seed_csv": str(OUT_CSV),
                "summary": str(OUT_SUMMARY),
                "audit": str(OUT_AUDIT),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
