"""
Phase 1 Analysis: Hub Sink De-bias + Randomized Baseline
Runs on existing cape_rq2_profiles.jsonl (no CAPE code changes needed)
"""
from __future__ import annotations
import json, random, sys
from collections import Counter, defaultdict
from pathlib import Path

CAPE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CAPE_ROOT.parents[1]
RESULTS_DIR = CAPE_ROOT / "results"

sys.path.insert(0, str(REPO_ROOT / "src"))

PROFILES_JSONL = RESULTS_DIR / "cape_rq2_profiles.jsonl"
OUT_DIR        = RESULTS_DIR

# ── Load profiles ──────────────────────────────────────────────────────────────
print("Loading profiles...")
profiles = []
with PROFILES_JSONL.open() as f:
    for line in f:
        profiles.append(json.loads(line))
print(f"  N = {len(profiles):,}")

sources = [p for p in profiles if p["source_caps"]]
sinks   = [p for p in profiles if p["sink_caps"]]
print(f"  Sources: {len(sources):,}  Sinks: {len(sinks):,}")

# ── Step 2: Hub Sink De-bias ───────────────────────────────────────────────────
print("\n=== STEP 2: Hub Sink Analysis ===")

# Count how many sources each sink pairs with (explicit channel)
def has_explicit_channel(p):
    return bool(p["channel_caps"])

def count_explicit_pairs(src_list, snk_list):
    """Count directed pairs where source has channel_caps overlapping with sink."""
    n = 0
    for src in src_list:
        src_ch = set(src["channel_caps"])
        for snk in snk_list:
            snk_ch = set(snk["channel_caps"])
            if src_ch & snk_ch:
                n += 1
    return n

# Build sink → how many explicit-channel source pairs
sink_pair_counts: dict[str, int] = Counter()
for src in sources:
    src_ch = set(src["channel_caps"])
    if not src_ch:
        continue
    for snk in sinks:
        snk_ch = set(snk["channel_caps"])
        if src_ch & snk_ch:
            sink_pair_counts[snk["name"]] += 1

# Top hub sinks
print("\nTop-20 Sink Hubs (by number of explicit-ch source pairs they appear in):")
for name, cnt in Counter(sink_pair_counts).most_common(20):
    print(f"  {cnt:6,}  {name}")

# Define hub threshold: sinks that appear in > 1% of all explicit-ch pairs
total_explicit = sum(sink_pair_counts.values())
HUB_THRESHOLD_PCT = 1.0
hub_threshold = int(total_explicit * HUB_THRESHOLD_PCT / 100)
hub_sinks = {name for name, cnt in sink_pair_counts.items() if cnt >= hub_threshold}
print(f"\nHub sinks (>{HUB_THRESHOLD_PCT}% of pairs each): {len(hub_sinks)}")
for h in sorted(hub_sinks):
    print(f"  {h}: {sink_pair_counts[h]:,} pairs ({100*sink_pair_counts[h]/total_explicit:.1f}%)")

# Recount excluding hubs
non_hub_sinks = [p for p in sinks if p["name"] not in hub_sinks]
print(f"\nSink count: total={len(sinks):,}  hub={len(hub_sinks)}  non-hub={len(non_hub_sinks):,}")

# Pair count excluding hub sinks
non_hub_pairs = sum(
    cnt for name, cnt in sink_pair_counts.items() if name not in hub_sinks
)
hub_pairs = sum(
    cnt for name, cnt in sink_pair_counts.items() if name in hub_sinks
)
print(f"\nExplicit-channel pair breakdown:")
print(f"  Total:      {total_explicit:,}")
print(f"  Hub-driven: {hub_pairs:,} ({100*hub_pairs/total_explicit:.1f}%)")
print(f"  Non-hub:    {non_hub_pairs:,} ({100*non_hub_pairs/total_explicit:.1f}%)")

# ── Step 3: Randomized Baseline ────────────────────────────────────────────────
print("\n=== STEP 3: Randomized Baseline (N=100 shuffles) ===")

# Collect all source_caps and channel_caps from all profiles
all_source_sets = [p["source_caps"] for p in profiles]
all_sink_sets   = [p["sink_caps"]   for p in profiles]
all_channel_sets= [p["channel_caps"]for p in profiles]

def count_pairs_from_labels(source_sets, sink_sets, channel_sets):
    """Count explicit-channel toxic pairs from capability label sets."""
    # Build index: channel → profiles with that channel
    src_profiles = [(i, set(s), set(c)) for i, (s, c) in enumerate(zip(source_sets, channel_sets))
                    if s]  # has source
    snk_profiles = [(i, set(s), set(c)) for i, (s, c) in enumerate(zip(sink_sets, channel_sets))
                    if s]  # has sink
    
    n_pairs = 0
    for si, src_caps, src_ch in src_profiles:
        for ti, snk_caps, snk_ch in snk_profiles:
            if si != ti and src_ch & snk_ch:
                n_pairs += 1
    return n_pairs

# Observed (fast recompute)
print("  Computing observed count...", end=" ", flush=True)
observed = total_explicit
print(f"{observed:,}")

# Baseline: shuffle channel assignments N times
N_SHUFFLE = 50  # 100 is slow, 50 is sufficient for a p-value
print(f"  Running {N_SHUFFLE} random shuffles (this may take a few minutes)...")
random.seed(42)
baseline_counts = []

for i in range(N_SHUFFLE):
    # Shuffle channel sets (reassign randomly)
    shuffled_channels = all_channel_sets.copy()
    random.shuffle(shuffled_channels)
    
    # Fast count using label sets
    src_profiles = [(j, set(all_source_sets[j]), set(shuffled_channels[j]))
                    for j in range(len(profiles)) if all_source_sets[j]]
    snk_profiles = [(j, set(all_sink_sets[j]), set(shuffled_channels[j]))
                    for j in range(len(profiles)) if all_sink_sets[j]]
    
    n = 0
    for si, sc, sch in src_profiles:
        for ti, sk, skch in snk_profiles:
            if si != ti and sch & skch:
                n += 1
    baseline_counts.append(n)
    
    if (i+1) % 10 == 0:
        print(f"    {i+1}/{N_SHUFFLE} done, last={n:,}", flush=True)

mean_baseline = sum(baseline_counts) / len(baseline_counts)
std_baseline  = (sum((x - mean_baseline)**2 for x in baseline_counts) / len(baseline_counts)) ** 0.5
ratio = observed / mean_baseline if mean_baseline > 0 else float('inf')
z_score = (observed - mean_baseline) / std_baseline if std_baseline > 0 else float('inf')

print(f"\n  Observed pairs:  {observed:,}")
print(f"  Baseline mean:   {mean_baseline:,.0f}")
print(f"  Baseline std:    {std_baseline:,.0f}")
print(f"  Ratio (obs/exp): {ratio:.2f}x")
print(f"  Z-score:         {z_score:.1f}σ")

# Save results
result = {
    "step2_hub_analysis": {
        "total_explicit_pairs": total_explicit,
        "hub_sinks": list(hub_sinks),
        "hub_pairs": hub_pairs,
        "non_hub_pairs": non_hub_pairs,
        "top_20_sinks": dict(Counter(sink_pair_counts).most_common(20)),
    },
    "step3_baseline": {
        "observed": observed,
        "n_shuffles": N_SHUFFLE,
        "baseline_mean": round(mean_baseline),
        "baseline_std": round(std_baseline),
        "ratio": round(ratio, 3),
        "z_score": round(z_score, 1),
        "baseline_samples": baseline_counts[:10],  # first 10 for reference
    }
}
out = OUT_DIR / "phase1_results.json"
out.write_text(json.dumps(result, indent=2))
print(f"\nSaved: {out}")
