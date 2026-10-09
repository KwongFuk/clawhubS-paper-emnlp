#!/usr/bin/env python3
"""
analyze_expanded.py — Analyze results from expanded bias experiments (P9-P16)
============================================================================

Reads exp3_*.json results from the results/ directory and produces:
1. Per-perturbation δ_API comparison vs baseline
2. Effect size (Cohen's d) for keyword injection
3. Brand preference cross-tabulation
4. LaTeX-ready tables for the paper
"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import math

RESULTS_DIR = Path(__file__).parent / "results"


def load_results():
    """Load all exp3_ results and baseline."""
    results = {}
    for f in sorted(RESULTS_DIR.glob("exp3_*.json")):
        data = json.loads(f.read_text())
        key = f"{data['cluster']}_{data['condition']}"
        results[key] = data
    
    # Load baseline
    baseline_file = RESULTS_DIR / "exp1_gemini-3.1-pro_run1_summary.json"
    baseline = {}
    if baseline_file.exists():
        bl = json.loads(baseline_file.read_text())
        for cluster_name, cluster_data in bl.get("clusters", {}).items():
            baseline[cluster_name] = {
                "delta_api": cluster_data.get("delta_api", 0),
                "selection_dist": cluster_data.get("selection_dist", {}),
            }
    
    return results, baseline


def cohen_d(baseline_delta, perturbed_delta, n=25):
    """Approximate Cohen's d for δ_API shift."""
    # Assume selection is Bernoulli-like, σ ≈ sqrt(δ*(1-δ)/n)
    sigma = math.sqrt(max(baseline_delta, 0.01) * (1 - max(baseline_delta, 0.01)) / n)
    if sigma == 0:
        return 0
    return (perturbed_delta - baseline_delta) / sigma


def analyze_h1(results, baseline):
    """H1: Performance/cost keyword injection analysis."""
    print("\n" + "="*70)
    print("H1: Performance & Cost Keyword Injection")
    print("="*70)
    
    print(f"\n{'Cluster':<20} {'Condition':<15} {'δ_API':>8} {'Baseline':>10} {'Δ':>8} {'Effect':>8}")
    print("-"*70)
    
    for cluster in ["web_search", "image_generation", "video_generation", "text_to_speech",
                     "speech_to_text", "github_management", "music_generation", "notion_management"]:
        bl_delta = baseline.get(cluster, {}).get("delta_api", 0)
        
        for condition in ["P9_perf", "P10_cost", "P9ctrl_neutral"]:
            key = f"{cluster}_{condition}"
            if key not in results:
                continue
            
            r = results[key]
            delta = r["delta_api"]
            diff = delta - bl_delta
            d = cohen_d(bl_delta, delta)
            
            print(f"{cluster:<20} {condition:<15} {delta:>8.4f} {bl_delta:>10.4f} {diff:>+8.4f} {d:>+8.2f}")
    
    # Target skill selection shift
    print(f"\n{'Cluster':<20} {'Condition':<15} {'Target Skill':>25} {'Baseline %':>10} {'After %':>10} {'Lift':>8}")
    print("-"*90)
    
    for cluster in ["web_search", "image_generation", "video_generation", "text_to_speech"]:
        bl_dist = baseline.get(cluster, {}).get("selection_dist", {})
        
        for condition in ["P9_perf", "P10_cost", "P9ctrl_neutral"]:
            key = f"{cluster}_{condition}"
            if key not in results:
                continue
            
            r = results[key]
            # Find the target skill (the one that was injected)
            for s in r.get("skills", []):
                name = s["name"]
                bl_share = bl_dist.get(name, 0)
                new_share = r["selection_dist"].get(name, 0)
                if new_share > bl_share + 0.05:  # >5% lift
                    lift = new_share - bl_share
                    print(f"{cluster:<20} {condition:<15} {name:>25} {bl_share:>9.1%} {new_share:>9.1%} {lift:>+7.1%}")


def analyze_h2(results, baseline):
    """H2: Self-brand preference analysis."""
    print("\n" + "="*70)
    print("H2: Self-Brand Preference Test")
    print("="*70)
    
    brands = ["OpenAI", "Google", "Anthropic", "Meta", "Open-Source"]
    
    for cluster in ["web_search", "image_generation", "text_to_speech", "video_generation"]:
        print(f"\n--- {cluster} ---")
        
        for condition in ["P11_brand", "P12_antibrand"]:
            key = f"{cluster}_{condition}"
            if key not in results:
                continue
            
            r = results[key]
            print(f"\n  {condition}:")
            
            # Map skill names to brands
            dist = r["selection_dist"]
            for name, share in sorted(dist.items(), key=lambda x: -x[1]):
                brand = "?"
                for b in brands:
                    if b.lower() in name.lower():
                        brand = b
                        break
                bar = "█" * int(share * 40)
                print(f"    {name:<30} {share:>6.1%}  {bar}")


def analyze_h5_h6(results, baseline):
    """H5+H6: Social proof and authority analysis."""
    print("\n" + "="*70)
    print("H5+H6: Social Proof & Authority Injection")
    print("="*70)
    
    print(f"\n{'Cluster':<20} {'Condition':<18} {'Target Skill':>25} {'Before':>8} {'After':>8} {'Lift':>8}")
    print("-"*90)
    
    for cluster in ["web_search", "image_generation", "text_to_speech", "video_generation"]:
        bl_dist = baseline.get(cluster, {}).get("selection_dist", {})
        
        for condition in ["P15_socialproof", "P16_authority"]:
            key = f"{cluster}_{condition}"
            if key not in results:
                continue
            
            r = results[key]
            for name in r["selection_dist"]:
                bl_share = bl_dist.get(name, 0)
                new_share = r["selection_dist"][name]
                if new_share > bl_share + 0.03:
                    lift = new_share - bl_share
                    print(f"{cluster:<20} {condition:<18} {name:>25} {bl_share:>7.1%} {new_share:>7.1%} {lift:>+7.1%}")


def generate_latex_table(results, baseline):
    """Generate LaTeX table for paper."""
    print("\n" + "="*70)
    print("LaTeX Table (copy to paper)")
    print("="*70)
    
    print(r"""
\begin{table}[t]
\centering
\small
\caption{Effect of description manipulation on skill selection.
$\delta_{\text{API}}$ measures deviation from uniform selection (0=fair, 1=concentrated).
Target is the least-selected skill receiving the injection.}
\label{tab:manipulation}
\begin{tabular}{llcccc}
\toprule
\textbf{Cluster} & \textbf{Perturbation} & \textbf{$\delta_{\text{API}}$} & \textbf{Baseline} & \textbf{$\Delta$} & \textbf{Target Lift} \\
\midrule""")
    
    for cluster in ["web_search", "image_generation", "text_to_speech", "video_generation"]:
        bl_delta = baseline.get(cluster, {}).get("delta_api", 0)
        bl_dist = baseline.get(cluster, {}).get("selection_dist", {})
        
        first = True
        for condition, label in [("P9_perf", "Perf. keywords"), 
                                  ("P10_cost", "Cost keywords"),
                                  ("P9ctrl_neutral", "Neutral (ctrl)"),
                                  ("P15_socialproof", "Social proof"),
                                  ("P16_authority", "Authority")]:
            key = f"{cluster}_{condition}"
            if key not in results:
                continue
            
            r = results[key]
            delta = r["delta_api"]
            diff = delta - bl_delta
            
            # Find max lift
            max_lift = 0
            for name in r["selection_dist"]:
                bl_share = bl_dist.get(name, 0)
                new_share = r["selection_dist"][name]
                if new_share - bl_share > max_lift:
                    max_lift = new_share - bl_share
            
            cname = cluster.replace("_", r"\_") if first else ""
            bl_str = f"{bl_delta:.3f}" if first else ""
            first = False
            
            print(f"{cname} & {label} & {delta:.3f} & {bl_str} & {diff:+.3f} & {max_lift:+.1%} \\\\")
        
        print(r"\midrule")
    
    print(r"""\bottomrule
\end{tabular}
\end{table}""")


def main():
    results, baseline = load_results()
    
    if not results:
        print("No exp3_ results found in", RESULTS_DIR)
        print("Run run_expanded_experiments.py first.")
        return
    
    print(f"Loaded {len(results)} experiment results")
    print(f"Loaded {len(baseline)} baseline clusters")
    
    analyze_h1(results, baseline)
    analyze_h2(results, baseline)
    analyze_h5_h6(results, baseline)
    generate_latex_table(results, baseline)


if __name__ == "__main__":
    main()
