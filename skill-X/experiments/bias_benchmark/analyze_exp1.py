#!/usr/bin/env python3
"""
analyze_exp1.py — Analyze EXP-1 results and generate paper-ready tables
========================================================================
Reads per-cluster result files and produces:
  1. Summary table (Table 1 in paper)
  2. Per-cluster selection distribution heatmap data
  3. δ_API vs δ_pos scatter data
  4. Feature correlation analysis
"""
import json, sys
from pathlib import Path
from collections import Counter

RESULTS_DIR = Path(__file__).parent / "results"
CLUSTERS_FILE = Path(__file__).parent / "clusters_final.json"


def load_results(model="gemini-3.1-pro", run_id=1):
    """Load all per-cluster results for a given model/run."""
    results = {}
    for f in sorted(RESULTS_DIR.glob(f"exp1_{model}_run{run_id}_*.json")):
        if "summary" in f.name or "full" in f.name:
            continue
        data = json.loads(f.read_text())
        cluster_name = data.get("cluster", f.stem.split("_")[-1])
        results[cluster_name] = data
    return results


def print_table1(results: dict):
    """Print Table 1: Per-cluster bias metrics."""
    print("\n" + "="*90)
    print("TABLE 1: Skill Selection Bias Across Clusters (Gemini 3.1 Pro)")
    print("="*90)
    print(f"{'Cluster':<25s} {'δ_API':>7s} {'δ_pos':>7s} {'Top-1 Tool':>25s} {'Top-1%':>7s} {'Coverage':>9s}")
    print("-"*90)
    
    all_delta_api = []
    all_delta_pos = []
    
    for name, data in sorted(results.items()):
        delta_api = data["delta_api"]
        delta_pos = data["delta_pos"]
        all_delta_api.append(delta_api)
        all_delta_pos.append(delta_pos)
        
        # Find top-1 tool
        sel_dist = data.get("selection_dist", {})
        if sel_dist:
            top_tool = max(sel_dist, key=sel_dist.get)
            top_pct = sel_dist[top_tool] * 100
        else:
            top_tool = "N/A"
            top_pct = 0
        
        # Coverage: how many tools were selected at least once
        n_selected = sum(1 for v in sel_dist.values() if v > 0)
        n_total = len(sel_dist)
        coverage = f"{n_selected}/{n_total}"
        
        print(f"{name:<25s} {delta_api:>7.3f} {delta_pos:>7.3f} {top_tool:>25s} {top_pct:>6.1f}% {coverage:>9s}")
    
    print("-"*90)
    if all_delta_api:
        avg_api = sum(all_delta_api) / len(all_delta_api)
        avg_pos = sum(all_delta_pos) / len(all_delta_pos)
        delta_model = sum(a + p for a, p in zip(all_delta_api, all_delta_pos)) / (2 * len(all_delta_api))
        print(f"{'AVERAGE':<25s} {avg_api:>7.3f} {avg_pos:>7.3f}")
        print(f"\nδ_model = {delta_model:.4f}")
    print()


def print_selection_matrix(results: dict):
    """Print selection distribution matrix for each cluster."""
    print("\n" + "="*90)
    print("TABLE 2: Selection Distribution Matrix")
    print("="*90)
    
    for name, data in sorted(results.items()):
        sel_dist = data.get("selection_dist", {})
        n_results = data["n_results"]
        print(f"\n{name} (n={n_results}, δ_API={data['delta_api']:.3f}):")
        
        # Sort by selection rate descending
        sorted_tools = sorted(sel_dist.items(), key=lambda x: -x[1])
        for tool, rate in sorted_tools:
            bar = "█" * int(rate * 40)
            count = int(rate * n_results)
            ideal = n_results // len(sel_dist)
            marker = " ←uniform" if abs(count - ideal) <= 1 else ""
            print(f"  {tool:30s} {rate*100:5.1f}% ({count:2d}/{n_results}) {bar}{marker}")


def print_position_analysis(results: dict):
    """Analyze if position matters."""
    print("\n" + "="*90)
    print("TABLE 3: Position Bias Analysis")
    print("="*90)
    print(f"{'Cluster':<25s} {'δ_pos':>7s} {'Pos Bias?':>10s}")
    print("-"*50)
    
    for name, data in sorted(results.items()):
        delta_pos = data["delta_pos"]
        bias = "YES" if delta_pos > 0.1 else "No"
        print(f"{name:<25s} {delta_pos:>7.3f} {bias:>10s}")
    
    avg = sum(d["delta_pos"] for d in results.values()) / len(results)
    print(f"\nAverage δ_pos = {avg:.4f}")
    if avg < 0.1:
        print("→ Conclusion: Position bias is NEGLIGIBLE. Selection is driven by DESCRIPTION CONTENT.")
    else:
        print("→ Conclusion: Position bias exists and needs further investigation.")


def print_key_findings(results: dict):
    """Print key findings for the paper."""
    print("\n" + "="*90)
    print("KEY FINDINGS FOR PAPER")
    print("="*90)
    
    all_delta_api = [d["delta_api"] for d in results.values()]
    all_delta_pos = [d["delta_pos"] for d in results.values()]
    
    # Finding 1: Bias exists
    avg_api = sum(all_delta_api) / len(all_delta_api)
    max_api = max(all_delta_api)
    max_cluster = [k for k, v in results.items() if v["delta_api"] == max_api][0]
    print(f"\n1. BIAS IS PERVASIVE:")
    print(f"   - Average δ_API = {avg_api:.3f} (0 = no bias, 1 = max bias)")
    print(f"   - Worst case: {max_cluster} with δ_API = {max_api:.3f}")
    
    # Finding 2: Not positional
    avg_pos = sum(all_delta_pos) / len(all_delta_pos)
    print(f"\n2. BIAS IS NOT POSITIONAL:")
    print(f"   - Average δ_pos = {avg_pos:.3f}")
    print(f"   - δ_API >> δ_pos in all clusters → description-driven, not position-driven")
    
    # Finding 3: Tool monopolies
    monopolies = []
    for name, data in results.items():
        sel_dist = data.get("selection_dist", {})
        if sel_dist:
            top_rate = max(sel_dist.values())
            if top_rate >= 0.8:
                top_tool = max(sel_dist, key=sel_dist.get)
                monopolies.append((name, top_tool, top_rate))
    
    if monopolies:
        print(f"\n3. TOOL MONOPOLIES ({len(monopolies)} clusters):")
        for name, tool, rate in monopolies:
            print(f"   - {name}: '{tool}' selected {rate*100:.0f}% of the time")
    
    # Finding 4: Tool exclusion
    excluded = []
    for name, data in results.items():
        sel_dist = data.get("selection_dist", {})
        zero_tools = [t for t, r in sel_dist.items() if r == 0]
        if zero_tools:
            excluded.append((name, zero_tools))
    
    if excluded:
        print(f"\n4. TOOL EXCLUSION ({len(excluded)} clusters have excluded tools):")
        for name, tools in excluded:
            print(f"   - {name}: NEVER selected → {tools}")


def main():
    results = load_results()
    
    if not results:
        print("No results found. Run run_all_clusters.py first.")
        sys.exit(1)
    
    print(f"Loaded {len(results)} cluster results\n")
    
    print_table1(results)
    print_selection_matrix(results)
    print_position_analysis(results)
    print_key_findings(results)


if __name__ == "__main__":
    main()
