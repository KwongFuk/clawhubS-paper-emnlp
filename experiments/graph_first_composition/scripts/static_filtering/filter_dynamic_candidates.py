#!/usr/bin/env python3
"""Ecological Plausibility Filter — narrows v3 top-10k to dynamic experiment candidates.

Pipeline (no co-installation data available):
  Step 1  channel_confidence ≥ MIN_CONF (channel attribution well-supported)
  Step 2  sink installs_all_time ≥ MIN_SINK_INSTALLS (real users install this sink)
  Step 3  source installs_all_time ≥ MIN_SRC_INSTALLS (real users install this source)
  Step 4  Semantic coherence filter: require at least one shared keyword cluster
          between source and sink descriptions (not purely accidental overlap)
  Step 5  Plausibility score = weighted sum of normalized signals
  Step 6  Output top-N sorted by plausibility, with manual-review annotations
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]

sys.path.insert(0, str(PROJECT_ROOT / "src"))

from clawhub_analyzer.pair_plausibility import (  # noqa: E402
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_SINK_INSTALLS,
    DEFAULT_MIN_SOURCE_INSTALLS,
    ecological_filter_reason,
    plausibility_score,
    row_skill_text,
    shared_clusters,
)

V3_CSV = EXPERIMENT_ROOT / "data/analysis_bcr_cis_v3/bcr_cis_v3_pairs.csv"
DB_PATH = PROJECT_ROOT / "data/experiment1_split/merged.sqlite3"
OUT_DIR = EXPERIMENT_ROOT / "data/analysis_bcr_cis_v3"
OUT_CANDIDATES = OUT_DIR / "dynamic_experiment_candidates.csv"
OUT_REPORT = OUT_DIR / "dynamic_experiment_report.md"

# ---------------------------------------------------------------------------
# Filter thresholds
# ---------------------------------------------------------------------------
MIN_CONF = DEFAULT_MIN_CONFIDENCE
MIN_SINK_INSTALLS = DEFAULT_MIN_SINK_INSTALLS
MIN_SRC_INSTALLS = DEFAULT_MIN_SOURCE_INSTALLS
TOP_N = 60


def load_skill_meta() -> dict[str, dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT skill_id, name, description, tags, metadata_systems, "
        "installs_all_time, stars, is_suspicious, is_deprecated FROM skills"
    ).fetchall()
    conn.close()
    return {row["skill_id"]: dict(row) for row in rows}


def main() -> None:
    print("Loading v3 pairs ...")
    with V3_CSV.open(encoding="utf-8") as f:
        pairs = list(csv.DictReader(f))
    print(f"  {len(pairs):,} pairs loaded")

    print("Loading skill metadata ...")
    meta = load_skill_meta()
    max_installs = max(m.get("installs_all_time") or 0 for m in meta.values())
    print(f"  {len(meta):,} skills, max installs = {max_installs}")

    print("Applying filters ...")
    step1 = step2 = step3 = step4 = 0
    results: list[dict] = []

    for p in pairs:
        conf = float(p.get("channel_confidence") or 0)
        src_id = p["source_skill_id"]
        snk_id = p["sink_skill_id"]
        src_meta = meta.get(src_id, {})
        snk_meta = meta.get(snk_id, {})

        src_installs = int(src_meta.get("installs_all_time") or 0)
        snk_installs = int(snk_meta.get("installs_all_time") or 0)

        # Step 1: channel confidence
        if conf < MIN_CONF:
            step1 += 1
            continue

        # Step 2: sink installs
        if snk_installs < MIN_SINK_INSTALLS:
            step2 += 1
            continue

        # Step 3: source installs
        if src_installs < MIN_SRC_INSTALLS:
            step3 += 1
            continue

        # Step 4: semantic coherence — enforced as a hard ecological filter
        if "shared_clusters" in p and p["shared_clusters"] not in ("", None):
            clusters = int(float(p["shared_clusters"]))
        else:
            src_text = row_skill_text(src_meta)
            snk_text = row_skill_text(snk_meta)
            clusters = shared_clusters(src_text, snk_text)
        if clusters < 1:
            step4 += 1
            continue

        # Compute plausibility score
        score = float(p["plausibility_score"]) if p.get("plausibility_score") else plausibility_score(
            conf,
            src_installs,
            snk_installs,
            clusters,
            max_installs,
        )
        ecology_reason = ecological_filter_reason(
            conf=conf,
            src_installs=src_installs,
            snk_installs=snk_installs,
            clusters=clusters,
            min_confidence=MIN_CONF,
            min_source_installs=MIN_SRC_INSTALLS,
            min_sink_installs=MIN_SINK_INSTALLS,
        )

        results.append({
            "plausibility_score":  score,
            "channel_confidence":  conf,
            "shared_clusters":     clusters,
            "source_skill_id":     src_id,
            "sink_skill_id":       snk_id,
            "carrier_type":        p["carrier_type"],
            "source_type":         p["source_type"],
            "sink_type":           p["sink_type"],
            "source_domain":       p["source_domain"],
            "sink_domain":         p["sink_domain"],
            "cis_tier":            p["cis_tier"],
            "ph_score":            float(p.get("ph_score") or 0),
            "src_installs":        src_installs,
            "snk_installs":        snk_installs,
            "ecological_reason":   ecology_reason or "",
            "is_new_arrival":      p.get("is_new_arrival", ""),
            "pair_recency_days":   p.get("pair_recency_days", ""),
            "shared_channels":     p["shared_channels"],
            "shared_objects":      p.get("shared_objects", ""),
            "snk_name":            snk_meta.get("name", ""),
            "src_name":            src_meta.get("name", ""),
            "snk_description":     (snk_meta.get("description") or "")[:120],
            "src_description":     (src_meta.get("description") or "")[:120],
        })

    print(f"\nFilter funnel:")
    print(f"  Input:                {len(pairs):,}")
    print(f"  Removed (low conf):   {step1:,}")
    print(f"  Removed (sink inst):  {step2:,}")
    print(f"  Removed (src inst):   {step3:,}")
    print(f"  Removed (no cluster): {step4:,}")
    print(f"  Passed all filters:   {len(results):,}")

    # Sort by plausibility desc
    results.sort(key=lambda r: (-r["plausibility_score"], -r["src_installs"]))
    top = results[:TOP_N]

    # Write CSV
    if top:
        with OUT_CANDIDATES.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(top[0].keys()))
            writer.writeheader()
            writer.writerows(top)
    print(f"\nWrote {len(top)} candidates → {OUT_CANDIDATES.name}")

    # Write markdown report
    lines = [
        "# Dynamic Experiment Candidates",
        "",
        "> Source: BCR+CIS v3 top-10k → ecological plausibility filter",
        "",
        "## Filter Funnel",
        "",
        "| Step | Filter | Removed | Remaining |",
        "|---|---|---:|---:|",
        f"| 0 | v3 input | — | {len(pairs):,} |",
        f"| 1 | channel_confidence ≥ {MIN_CONF} | {step1:,} | {len(pairs)-step1:,} |",
        f"| 2 | sink installs ≥ {MIN_SINK_INSTALLS} | {step2:,} | {len(pairs)-step1-step2:,} |",
        f"| 3 | source installs ≥ {MIN_SRC_INSTALLS} | {step3:,} | {len(pairs)-step1-step2-step3:,} |",
        f"| 4 | semantic cluster overlap ≥ 1 | {step4:,} | **{len(results):,}** |",
        f"| 5 | top-{TOP_N} by plausibility score | — | **{len(top):,}** |",
        "",
        "## Plausibility Score Components",
        "",
        "| Component | Weight | Meaning |",
        "|---|---|---|",
        "| `channel_confidence` | 1× | % of text signals supporting the channel |",
        "| `src_installs` (normalized) | 1× | Real source usage breadth |",
        "| `snk_installs` (normalized) | 1× | Real sink usage breadth |",
        "| `shared_clusters` (≤3, normalized) | 1× | Semantic topic overlap between src+snk |",
        "",
        "**Score range**: 0–4. Higher = more ecologically plausible pair.",
        "",
        "---",
        "",
        f"## Top {len(top)} Candidates for Manual Review",
        "",
        "| # | Source | Sink | Carrier | Sink Type | Src→Sink Domain | Plausibility | Conf | Clusters | Src★ | Snk★ |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]

    for i, r in enumerate(top, 1):
        domain_path = f"{r['source_domain']}→{r['sink_domain']}"
        lines.append(
            f"| {i} | `{r['source_skill_id']}` | `{r['sink_skill_id']}` "
            f"| `{r['carrier_type']}` | `{r['sink_type']}` "
            f"| {domain_path} "
            f"| {r['plausibility_score']} | {r['channel_confidence']} "
            f"| {r['shared_clusters']} | {r['src_installs']} | {r['snk_installs']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Next Step: Manual Triage",
        "",
        "For each candidate above, judge in ≤10 seconds:",
        "",
        "> **Would a real user plausibly co-install BOTH skills for a single workflow?**",
        "",
        "- ✅ YES → dynamic experiment candidate",
        "- ❌ NO → drop (structural FP)",
        "",
        "Expected yield: ~20–30 plausible pairs from the above list.",
    ]

    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote report → {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
