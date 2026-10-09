#!/usr/bin/env python3
"""
analyze_exp2.py — Analyze EXP-2 perturbation results
=====================================================
Reads per-(cluster, perturbation) result files and produces:
  1. Perturbation impact table (Table 6 in paper)
  2. Per-component causal attribution
  3. P7 description-transfer analysis
"""
import json, sys
from pathlib import Path
from collections import defaultdict

RESULTS_DIR = Path(__file__).parent / "results"
MODEL = "gemini-3.1-pro"
RUN_ID = 1


def load_exp2_results():
    """Load all EXP-2 per-pair results."""
    results = {}
    for f in sorted(RESULTS_DIR.glob(f"exp2_{MODEL}_run{RUN_ID}_*_P*.json")):
        data = json.loads(f.read_text())
        key = f"{data['cluster']}_{data['perturbation']}"
        results[key] = data
    return results


def print_table6(results: dict):
    """Print Table 6: Perturbation impact."""
    print("\n" + "=" * 85)
    print("TABLE 6: Causal Impact of Description Perturbation")
    print("=" * 85)
    print(f"{'Perturbation':<30s} {'Component':>10s} {'δ_base':>8s} {'δ_pert':>8s} {'Δδ':>8s} {'Causal?':>8s}")
    print("-" * 85)

    # Group by perturbation
    by_pert = defaultdict(list)
    for key, data in results.items():
        pid = data["perturbation"]
        by_pert[pid].append(data)

    for pid in sorted(by_pert.keys()):
        items = by_pert[pid]
        label = items[0].get("perturbation_label", pid)
        component = "Name" if pid in ("P1", "P2", "P3") else "Desc"
        
        avg_base = sum(d.get("baseline_delta_api", 0) for d in items) / len(items)
        avg_pert = sum(d["delta_api"] for d in items) / len(items)
        avg_shift = avg_pert - avg_base
        
        causal = "YES" if abs(avg_shift) > 0.1 else "no"
        marker = " ★" if abs(avg_shift) > 0.2 else ""
        
        print(f"  {label:<28s} {component:>10s} {avg_base:>8.3f} {avg_pert:>8.3f} "
              f"{avg_shift:>+8.3f} {causal:>8s}{marker}")
    
    print("-" * 85)
    
    # Overall
    name_shifts = [d["delta_api"] - d.get("baseline_delta_api", 0)
                   for d in results.values() if d["perturbation"] in ("P1", "P2", "P3")]
    desc_shifts = [d["delta_api"] - d.get("baseline_delta_api", 0)
                   for d in results.values() if d["perturbation"] in ("P4", "P5", "P6", "P7")]
    
    if name_shifts:
        print(f"  {'NAME perturbations (avg)':<28s} {'':>10s} {'':>8s} {'':>8s} "
              f"{sum(name_shifts)/len(name_shifts):>+8.3f}")
    if desc_shifts:
        print(f"  {'DESC perturbations (avg)':<28s} {'':>10s} {'':>8s} {'':>8s} "
              f"{sum(desc_shifts)/len(desc_shifts):>+8.3f}")


def print_p7_analysis(results: dict):
    """Analyze P7 (description transfer) in detail."""
    p7_data = [d for d in results.values() if d["perturbation"] == "P7"]
    
    if not p7_data:
        print("\nNo P7 data available yet.")
        return
    
    print("\n" + "=" * 85)
    print("P7 DESCRIPTION TRANSFER ANALYSIS (Critical Test)")
    print("=" * 85)
    print("If the previously-excluded skill gains majority share after receiving")
    print("the dominant skill's description, it proves DESCRIPTION drives routing.\n")
    
    for d in p7_data:
        cluster = d["cluster"]
        sel_dist = d.get("selection_dist", {})
        orig_names = d.get("original_names", [])
        most_idx = d.get("most_selected_idx", 0)
        least_idx = d.get("least_selected_idx", -1)
        
        print(f"Cluster: {cluster}")
        if orig_names:
            print(f"  Swap: {orig_names[most_idx]} ↔ {orig_names[least_idx]} descriptions")
        
        print(f"  Perturbed selection distribution:")
        for name, rate in sorted(sel_dist.items(), key=lambda x: -x[1]):
            bar = "█" * int(rate * 30)
            print(f"    {name:30s} {rate*100:5.1f}% {bar}")
        
        # Check if description carried the routing advantage
        baseline_file = RESULTS_DIR / f"exp1_{MODEL}_run{RUN_ID}_{cluster}.json"
        if baseline_file.exists():
            baseline = json.loads(baseline_file.read_text())
            bl_dist = baseline.get("selection_dist", {})
            print(f"\n  Comparison with baseline:")
            print(f"    {'Skill':<30s} {'Baseline':>10s} {'After P7':>10s} {'Change':>10s}")
            for name in sel_dist:
                bl = bl_dist.get(name, 0)
                pt = sel_dist.get(name, 0)
                change = pt - bl
                print(f"    {name:<30s} {bl:>9.1%} {pt:>9.1%} {change:>+9.1%}")
        print()


def print_key_findings(results: dict):
    """Print key findings for the paper."""
    print("\n" + "=" * 85)
    print("KEY FINDINGS FOR PAPER")
    print("=" * 85)
    
    by_pert = defaultdict(list)
    for data in results.values():
        pid = data["perturbation"]
        shift = data["delta_api"] - data.get("baseline_delta_api", 0)
        by_pert[pid].append(shift)
    
    name_perts = ["P1", "P2", "P3"]
    desc_perts = ["P4", "P5", "P6", "P7"]
    
    name_avg = sum(s for pid in name_perts for s in by_pert.get(pid, []))
    name_n = sum(len(by_pert.get(pid, [])) for pid in name_perts)
    desc_avg = sum(s for pid in desc_perts for s in by_pert.get(pid, []))
    desc_n = sum(len(by_pert.get(pid, [])) for pid in desc_perts)
    
    if name_n > 0:
        print(f"\n1. NAME PERTURBATIONS:")
        print(f"   Average Δδ = {name_avg/name_n:+.4f} (n={name_n})")
        print(f"   → {'Description, NOT name, drives routing' if abs(name_avg/name_n) < 0.1 else 'Name has significant causal effect'}")
    
    if desc_n > 0:
        print(f"\n2. DESCRIPTION PERTURBATIONS:")
        print(f"   Average Δδ = {desc_avg/desc_n:+.4f} (n={desc_n})")
        print(f"   → {'Description has STRONG causal effect on routing' if abs(desc_avg/desc_n) > 0.1 else 'Description effect is weak'}")
    
    # P7 specific
    if "P7" in by_pert:
        p7_avg = sum(by_pert["P7"]) / len(by_pert["P7"])
        print(f"\n3. P7 DESCRIPTION TRANSFER (strongest test):")
        print(f"   Average Δδ = {p7_avg:+.4f}")
        if abs(p7_avg) > 0.2:
            print(f"   → CAUSAL: Swapping descriptions transfers the routing advantage!")
        else:
            print(f"   → Weak: Description alone doesn't fully explain routing preference")


def main():
    results = load_exp2_results()
    
    if not results:
        print("No EXP-2 results found yet.")
        print("Run: PYTHONUNBUFFERED=1 python3 -u run_perturbation_exp.py")
        sys.exit(0)
    
    print(f"Loaded {len(results)} perturbation pairs\n")
    
    print_table6(results)
    print_p7_analysis(results)
    print_key_findings(results)


if __name__ == "__main__":
    main()
