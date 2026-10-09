#!/usr/bin/env python3
"""
Name Randomization Control Experiment
======================================
Two approaches:
1. POST-HOC: Re-analyze existing raw results with shuffled name mappings
   to estimate the counterfactual distribution (statistical approach)
   
2. ACTIVE: Re-run 3 original clusters with anonymized "Tool_A ... Tool_E" names
   to directly compare named vs. anonymous selection.

For the paper, Approach 2 (active control) is most convincing.
We'll use the Gemini API (same as original experiment).
"""
import json, os, re, time, random
from pathlib import Path
from collections import Counter
import subprocess

RESULTS_DIR = Path(__file__).parent / "results"
CLUSTERS_FINAL = Path(__file__).parent / "clusters_final.json"
ANON_DIR = RESULTS_DIR / "name_randomization"
ANON_DIR.mkdir(exist_ok=True)

# Clusters to re-run with anonymized names (the 3 paper clusters + 2 more)
TARGET_CLUSTERS = ["web_search", "image_generation", "video_generation",
                   "speech_to_text", "text_to_speech"]

# ── Load cluster metadata ────────────────────────────────────────────────────

with open(CLUSTERS_FINAL) as f:
    clusters = json.load(f)

# ── Load original results ────────────────────────────────────────────────────

def load_cluster_results(cluster_name):
    f = RESULTS_DIR / f"exp1_gemini-3.1-pro_run1_{cluster_name}.json"
    if not f.exists():
        return None
    with open(f) as fp:
        return json.load(fp)

# ── Load cluster tool descriptions ──────────────────────────────────────────

def load_cluster_descs(cluster_name):
    """Load the actual tool descriptions used in the experiment."""
    # Check if there's a cluster description cache
    desc_file = RESULTS_DIR / f"cluster_descs_{cluster_name}.json"
    if desc_file.exists():
        with open(desc_file) as f:
            return json.load(f)
    return None

# ── Post-hoc analysis: compute delta_API under name permutation ─────────────

def posthoc_name_shuffle_analysis(cluster_name, n_permutations=1000):
    """
    Statistical approach: take real selection results, relabel tool names
    randomly. Compute how much of the observed δ_API comes from description
    quality vs. name preference.
    
    Key insight: if names matter, then random relabeling should produce
    lower observed δ_API (because the "attractive" name now points to
    different descriptions). If names don't matter, δ_API stays similar.
    """
    data = load_cluster_results(cluster_name)
    if not data:
        print(f"  No data for {cluster_name}")
        return None
    
    results = data.get("results", [])
    tools = list(data["selection_dist"].keys())
    
    if not tools or not results:
        return None
    
    # Original selection frequency
    orig_counts = Counter(r["selected"] for r in results if r.get("selected"))
    n_total = sum(orig_counts.values())
    
    # Original δ_API
    orig_probs = [orig_counts.get(t, 0) / n_total for t in tools]
    uniform = 1.0 / len(tools)
    orig_delta = sum(abs(p - uniform) for p in orig_probs) / 2
    
    # Permutation test: randomly relabel tools, compute δ_API
    # (This tests: is the concentration explained by a single tool's identity?)
    perm_deltas = []
    for _ in range(n_permutations):
        perm = random.sample(tools, len(tools))
        mapping = dict(zip(tools, perm))
        perm_counts = Counter(mapping.get(r["selected"], r["selected"])
                              for r in results if r.get("selected"))
        perm_probs = [perm_counts.get(t, 0) / n_total for t in tools]
        perm_delta = sum(abs(p - uniform) for p in perm_probs) / 2
        perm_deltas.append(perm_delta)
    
    avg_perm = sum(perm_deltas) / len(perm_deltas)
    # If avg_perm ≈ orig_delta → bias is structural (in descriptions/content)
    # If avg_perm << orig_delta → bias concentrated on specific name
    
    return {
        "cluster": cluster_name,
        "n_tools": len(tools),
        "n_calls": n_total,
        "orig_delta_api": round(orig_delta, 4),
        "mean_perm_delta_api": round(avg_perm, 4),
        "ratio": round(avg_perm / orig_delta, 3) if orig_delta > 0 else None,
        "interpretation": (
            "description-driven" if avg_perm > 0.8 * orig_delta
            else "name-confounded"
        )
    }

# ── Active control: re-run with anonymized names via API ─────────────────────

ANON_PROMPT_TEMPLATE = """You are an AI agent assistant. A user has given you the following task:

"{query}"

You have access to these tools:
{tool_list}

Select the SINGLE most appropriate tool for this task.
Respond with ONLY the exact tool name, nothing else."""

def build_anon_tool_list(tools_with_descs, anon_mapping):
    """Build anonymized tool list: Tool_A, Tool_B etc."""
    lines = []
    for orig_name, anon_name in anon_mapping.items():
        desc = tools_with_descs.get(orig_name, "")
        lines.append(f"- {anon_name}: {desc}")
    return "\n".join(lines)

def call_gemini(prompt, model="gemini-2.5-flash"):
    """Call Gemini via the API (using openclaw or direct API)."""
    # Try openclaw first
    try:
        r = subprocess.run(
            ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
            capture_output=True, text=True, timeout=60
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            return data.get("outputs", [{}])[0].get("text", "").strip()
    except Exception:
        pass
    return None

# ── Main: Post-hoc analysis (no API needed) ─────────────────────────────────

def run_posthoc_analysis():
    print("="*60)
    print("POST-HOC NAME PERMUTATION ANALYSIS")
    print("Tests if δ_API structure comes from descriptions or names")
    print("="*60)
    
    all_results = []
    
    for cluster_name in TARGET_CLUSTERS:
        print(f"\nAnalyzing: {cluster_name}")
        result = posthoc_name_shuffle_analysis(cluster_name)
        if result:
            all_results.append(result)
            print(f"  Original δ_API:     {result['orig_delta_api']:.3f}")
            print(f"  Mean permuted δ_API: {result['mean_perm_delta_api']:.3f}")
            print(f"  Ratio (perm/orig):  {result['ratio']:.3f}")
            print(f"  → {result['interpretation']}")
    
    print("\n" + "="*60)
    print("AGGREGATE RESULTS")
    print("="*60)
    
    if all_results:
        avg_orig = sum(r["orig_delta_api"] for r in all_results) / len(all_results)
        avg_perm = sum(r["mean_perm_delta_api"] for r in all_results) / len(all_results)
        avg_ratio = sum(r["ratio"] for r in all_results if r["ratio"]) / len(all_results)
        
        print(f"Clusters analyzed: {len(all_results)}")
        print(f"Mean original δ_API:     {avg_orig:.3f}")
        print(f"Mean permuted δ_API:     {avg_perm:.3f}")
        print(f"Mean ratio (perm/orig):  {avg_ratio:.3f}")
        print()
        
        if avg_ratio > 0.85:
            print("CONCLUSION: Bias is structurally stable under name permutation.")
            print("This supports description-driven interpretation.")
        elif avg_ratio > 0.6:
            print("CONCLUSION: Partial name effect detected.")
        else:
            print("CONCLUSION: Strong name effect — confound present.")
        
        # Save results
        out = {
            "method": "posthoc_permutation_test",
            "n_permutations": 1000,
            "clusters": all_results,
            "aggregate": {
                "n_clusters": len(all_results),
                "mean_orig_delta_api": round(avg_orig, 4),
                "mean_perm_delta_api": round(avg_perm, 4),
                "mean_ratio": round(avg_ratio, 3),
            }
        }
        with open(ANON_DIR / "posthoc_permutation.json", "w") as fp:
            json.dump(out, fp, indent=2)
        print(f"\nSaved to: {ANON_DIR / 'posthoc_permutation.json'}")
    
    return all_results

# ── Compute full 10-cluster stats ────────────────────────────────────────────

def compute_all_clusters():
    """Compute aggregate stats for all 10 clusters."""
    print("\n" + "="*60)
    print("ALL 10-CLUSTER AGGREGATE STATISTICS")
    print("="*60)
    
    all_delta_api = []
    all_delta_pos = []
    cluster_rows = []
    
    for f in sorted(RESULTS_DIR.glob("exp1_gemini-3.1-pro_run1_*.json")):
        if "summary" in f.name:
            continue
        cluster_name = f.name.replace("exp1_gemini-3.1-pro_run1_", "").replace(".json", "")
        with open(f) as fp:
            data = json.load(fp)
        
        delta_api = data.get("delta_api", 0)
        delta_pos = data.get("delta_pos", 0)
        n_calls = data.get("n_results", 0)
        
        # Only include clusters with real data (n_calls > 0)
        if n_calls > 0:
            all_delta_api.append(delta_api)
            all_delta_pos.append(delta_pos)
            
            sel_dist = data.get("selection_dist", {})
            if sel_dist:
                never_selected = sum(1 for v in sel_dist.values() if v == 0)
                top1_skill = max(sel_dist, key=sel_dist.get)
                top1_pct = sel_dist[top1_skill] * 100
            else:
                never_selected = 0
                top1_skill = "?"
                top1_pct = 0
            
            cluster_rows.append({
                "cluster": cluster_name,
                "delta_api": delta_api,
                "delta_pos": delta_pos,
                "n_calls": n_calls,
                "never_selected": never_selected,
                "top1_skill": top1_skill,
                "top1_pct": round(top1_pct, 1)
            })
    
    print(f"\nClusters with complete data: {len(cluster_rows)}")
    print(f"\n{'Cluster':<25} {'δ_API':>6} {'δ_pos':>6} {'n':>5} {'Top-1':>25} {'%':>6}")
    print("-" * 80)
    for r in sorted(cluster_rows, key=lambda x: -x["delta_api"]):
        print(f"{r['cluster']:<25} {r['delta_api']:>6.3f} {r['delta_pos']:>6.3f} "
              f"{r['n_calls']:>5} {r['top1_skill']:>25} {r['top1_pct']:>5.1f}%")
    
    if all_delta_api:
        mean_api = sum(all_delta_api) / len(all_delta_api)
        mean_pos = sum(all_delta_pos) / len(all_delta_pos)
        n_pos_zero = sum(1 for x in all_delta_pos if x == 0)
        print(f"\n{'MEAN':<25} {mean_api:>6.3f} {mean_pos:>6.3f}")
        print(f"\nMean δ_API = {mean_api:.3f}")
        print(f"Mean δ_pos = {mean_pos:.3f}")
        print(f"δ_pos = 0 in {n_pos_zero}/{len(all_delta_pos)} clusters ({100*n_pos_zero/len(all_delta_pos):.0f}%)")
        
        # Save aggregate
        agg = {
            "n_clusters": len(cluster_rows),
            "mean_delta_api": round(mean_api, 4),
            "mean_delta_pos": round(mean_pos, 4),
            "pct_pos_zero": round(100*n_pos_zero/len(all_delta_pos), 1),
            "clusters": cluster_rows
        }
        with open(ANON_DIR / "all_clusters_aggregate.json", "w") as fp:
            json.dump(agg, fp, indent=2)
        
        return cluster_rows, mean_api, mean_pos
    return [], 0, 0

if __name__ == "__main__":
    import sys
    
    print("TrustDrift Bias Benchmark — Extended Analysis")
    print("=" * 60)
    
    # Step 1: Compute all-cluster stats
    cluster_rows, mean_api, mean_pos = compute_all_clusters()
    
    # Step 2: Name permutation post-hoc analysis
    print()
    perm_results = run_posthoc_analysis()
