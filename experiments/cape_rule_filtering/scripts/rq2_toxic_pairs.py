"""
RQ2 — Toxic Skill Pair Analysis
================================
Research Question: Are there large numbers of skill pairs in the marketplace
that are individually safe but dangerous when composed together?

Each skill is labeled with four binary capabilities:
  - untrusted_input  : exposes the agent to attacker-controlled content (A-type / Source)
  - sensitive_read   : reads private user data (A-type / Source)
  - critical_action  : executes high-impact actions (B-type / Sink)
  - exfil_channel    : can send data outside the agent's trust boundary (B-type / Sink)

A "toxic pair" is any (source_skill, sink_skill) that satisfies:
  - source has untrusted_input OR sensitive_read
  - sink  has critical_action  OR exfil_channel
  - AND the two skills share a compatible semantic domain

Usage:
    .venv/bin/python experiments/cape_rule_filtering/scripts/rq2_toxic_pairs.py

Output:
    experiments/cape_rule_filtering/results/rq2_toxic_pairs_obvious.csv
    experiments/cape_rule_filtering/results/rq2_toxic_pairs_covert.csv
    experiments/cape_rule_filtering/results/rq2_summary.json
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

CAPE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CAPE_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from clawhub_analyzer.toxicity import (
    count_typed_pairs,
    load_capabilities,
    pair_category_potential_counts,
    randomized_typed_pair_baseline,
    rank_covert_pairs,
    rank_obvious_pairs,
    write_candidates_csv,
)

# ─── Config ──────────────────────────────────────────────────────────────────

DB_PATH = REPO_ROOT / "data/experiment1_split/merged.sqlite3"
RESULTS_DIR = CAPE_ROOT / "results"
TOP_K = 50           # pairs to write to CSV
BASELINE_ITERS = 100  # shuffles for null-model comparison
BASELINE_SEED = 42

# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Load capability labels for every skill
    print(f"Loading skills from {DB_PATH} …")
    caps = load_capabilities(DB_PATH)
    n = len(caps)
    print(f"  {n:,} skills loaded")

    # 2. Per-label rates
    n_untrusted = sum(1 for c in caps if c.untrusted_input)
    n_sensitive  = sum(1 for c in caps if c.sensitive_read)
    n_critical   = sum(1 for c in caps if c.critical_action)
    n_exfil      = sum(1 for c in caps if c.exfil_channel)
    n_source     = sum(1 for c in caps if c.overt_source())
    n_sink       = sum(1 for c in caps if c.overt_sink())
    n_covert_src = sum(1 for c in caps if c.covert_source())
    n_dual       = sum(1 for c in caps if c.overt_source() and c.overt_sink())

    print("\n=== Capability Label Rates ===")
    for label, count in [
        ("untrusted_input", n_untrusted),
        ("sensitive_read",  n_sensitive),
        ("critical_action", n_critical),
        ("exfil_channel",   n_exfil),
    ]:
        print(f"  {label:20s}: {count:,} / {n:,}  ({100*count/n:.1f}%)")

    print(f"\n  Source (A-type) skills : {n_source:,} ({100*n_source/n:.1f}%)")
    print(f"  Sink   (B-type) skills : {n_sink:,} ({100*n_sink/n:.1f}%)")
    print(f"  Covert-source skills   : {n_covert_src:,} ({100*n_covert_src/n:.1f}%)")
    print(f"  Dual-role skills       : {n_dual:,} ({100*n_dual/n:.1f}%)")

    # 3. Count domain-compatible typed pairs (observed)
    print("\n=== Typed Pair Counts (domain-compatible) ===")
    observed = count_typed_pairs(caps)
    total_observed = sum(observed.values())
    for cat, count in sorted(observed.items(), key=lambda x: -x[1]):
        print(f"  {cat:30s}: {count:,}")
    print(f"  {'TOTAL':30s}: {total_observed:,}")

    # 4. Upper-bound without domain filter
    potentials = pair_category_potential_counts(caps)
    total_potential = sum(potentials.values())
    print("\n=== Potential Pairs (no domain filter) ===")
    for cat, count in sorted(potentials.items(), key=lambda x: -x[1]):
        print(f"  {cat:30s}: {count:,}")
    print(f"  {'TOTAL':30s}: {total_potential:,}")

    # 5. Randomized null-model baseline
    print(f"\nRunning {BASELINE_ITERS}-iteration randomized baseline …")
    baseline_runs = randomized_typed_pair_baseline(caps, iterations=BASELINE_ITERS, seed=BASELINE_SEED)

    baseline_stats: dict[str, dict[str, float]] = {}
    print("\n=== Observed vs. Null-Model Baseline ===")
    for cat in ["overt_input_to_action", "overt_read_to_exfil",
                "overt_input_to_exfil",  "overt_read_to_action"]:
        vals  = [r[cat] for r in baseline_runs]
        mean_ = statistics.mean(vals)
        std_  = statistics.stdev(vals)
        ratio = observed[cat] / max(mean_, 1)
        baseline_stats[cat] = {"mean": mean_, "stdev": std_, "ratio": ratio}
        print(f"  {cat:30s}  observed={observed[cat]:,}  baseline={mean_:.0f}  ratio={ratio:.2f}x")

    # 6. Rank top obvious pairs
    print(f"\nRanking top-{TOP_K} obvious toxic pairs …")
    obvious = rank_obvious_pairs(caps, top_k=TOP_K, source_limit=500, sink_limit=500)
    obvious_csv = RESULTS_DIR / "rq2_toxic_pairs_obvious.csv"
    write_candidates_csv(obvious_csv, obvious)
    print(f"  Written → {obvious_csv}")

    # 7. Rank top covert pairs (via shared low-visibility channel)
    print(f"Ranking top-{TOP_K} covert toxic pairs …")
    covert = rank_covert_pairs(caps, top_k=TOP_K, source_limit=500, sink_limit=500)
    covert_csv = RESULTS_DIR / "rq2_toxic_pairs_covert.csv"
    write_candidates_csv(covert_csv, covert)
    print(f"  Written → {covert_csv}")

    # 8. Print illustrative examples
    print("\n=== Top-10 Obvious Toxic Pairs ===")
    for i, p in enumerate(obvious[:10], 1):
        print(f"  {i:2d}. [{p.category}]")
        print(f"       source: {p.source_name!r}  (↓{p.source_downloads:,})")
        print(f"       sink  : {p.sink_name!r}  (↓{p.sink_downloads:,})")
        print(f"       why   : {p.rationale}")

    print("\n=== Top-10 Covert Toxic Pairs ===")
    for i, p in enumerate(covert[:10], 1):
        print(f"  {i:2d}. [{p.category}]  medium={p.medium}")
        print(f"       source: {p.source_name!r}  (↓{p.source_downloads:,})")
        print(f"       sink  : {p.sink_name!r}  (↓{p.sink_downloads:,})")
        print(f"       why   : {p.rationale}")

    # 9. Write JSON summary
    summary = {
        "n_skills": n,
        "capability_rates": {
            "untrusted_input": n_untrusted,
            "sensitive_read":  n_sensitive,
            "critical_action": n_critical,
            "exfil_channel":   n_exfil,
        },
        "role_counts": {
            "source_skills":  n_source,
            "sink_skills":    n_sink,
            "covert_source":  n_covert_src,
            "dual_role":      n_dual,
        },
        "typed_pair_counts": observed,
        "typed_pair_totals": {
            "observed": total_observed,
            "potential": total_potential,
        },
        "baseline_comparison": {
            cat: {
                "observed":       observed[cat],
                "baseline_mean":  round(s["mean"]),
                "baseline_stdev": round(s["stdev"]),
                "ratio":          round(s["ratio"], 3),
            }
            for cat, s in baseline_stats.items()
        },
        "top_obvious_pairs": [
            {
                "category":         p.category,
                "source_skill_id":  p.source_skill_id,
                "source_name":      p.source_name,
                "source_downloads": p.source_downloads,
                "sink_skill_id":    p.sink_skill_id,
                "sink_name":        p.sink_name,
                "sink_downloads":   p.sink_downloads,
                "rationale":        p.rationale,
                "score":            round(p.score, 4),
            }
            for p in obvious[:20]
        ],
        "top_covert_pairs": [
            {
                "category":         p.category,
                "medium":           p.medium,
                "source_skill_id":  p.source_skill_id,
                "source_name":      p.source_name,
                "source_downloads": p.source_downloads,
                "sink_skill_id":    p.sink_skill_id,
                "sink_name":        p.sink_name,
                "sink_downloads":   p.sink_downloads,
                "rationale":        p.rationale,
                "score":            round(p.score, 4),
            }
            for p in covert[:20]
        ],
    }
    summary_path = RESULTS_DIR / "rq2_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nSummary JSON → {summary_path}")

    print("\n✓ RQ2 analysis complete.")


if __name__ == "__main__":
    main()
