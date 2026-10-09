#!/usr/bin/env python3
"""
run_name_ablation.py — Active Name-Anonymization / Name-Only / Desc-Only Causal Ablations
==========================================================================================
Directly addresses ARR W6: "selection concentration description-driven causal evidence insufficient"

Three NEW conditions per cluster (complement existing P7 desc-swap):
  1. NAME_ANON:  names → Tool A/B/C/D/E, descriptions unchanged
  2. NAME_ONLY:  names unchanged, all descriptions → uniform template
  3. DESC_ONLY:  names → Tool A/B/C/D/E, descriptions unchanged (= NAME_ANON duplicate check)

Run on 8 clusters × 25 calls × 3 conditions = 600 calls per model.

Usage:
  nohup python3 run_name_ablation.py > results/name_ablation.log 2>&1 &
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_expanded_experiments import run_experiment, load_clusters, RESULTS_DIR

MODEL_TAG = "gpt54mini"
GENERIC_NAMES = ["Tool A", "Tool B", "Tool C", "Tool D", "Tool E"]
UNIFORM_DESC = "A tool that performs the requested task. It accepts standard inputs and produces the expected output."

ALL_CLUSTERS = [
    "web_search", "image_generation", "video_generation", "text_to_speech",
    "speech_to_text", "github_management", "music_generation", "notion_management",
]


def make_name_anon(skills):
    """Replace all names with generic Tool A/B/C/D/E, keep descriptions."""
    new_skills = copy.deepcopy(skills)
    for i, s in enumerate(new_skills):
        s["name"] = GENERIC_NAMES[i % len(GENERIC_NAMES)]
    return new_skills


def make_name_only(skills):
    """Keep real names, replace all descriptions with uniform template."""
    new_skills = copy.deepcopy(skills)
    for s in new_skills:
        s["description"] = UNIFORM_DESC
    return new_skills


def make_desc_only(skills):
    """Generic names + real descriptions = isolate description effect."""
    new_skills = copy.deepcopy(skills)
    for i, s in enumerate(new_skills):
        s["name"] = GENERIC_NAMES[i % len(GENERIC_NAMES)]
    return new_skills


def run_condition(tag, cluster_name, skills, condition_name):
    """Run one condition and save results."""
    r = run_experiment(tag, cluster_name, skills)
    out_path = RESULTS_DIR / f"exp5_{MODEL_TAG}_{cluster_name}_{condition_name}.json"
    r["model"] = "openai-codex/gpt-5.4-mini"
    r["condition"] = condition_name
    with open(out_path, "w") as f:
        json.dump(r, f, indent=2)
    
    sel = r["selection_dist"]
    top = max(sel, key=sel.get) if sel else "?"
    share = sel.get(top, 0)
    print(f"  ✅ {condition_name:15} δ={r['delta_api']:.3f} top={top}({share:.0%})")
    return r


def main():
    clusters = load_clusters()
    
    print("=" * 70)
    print("NAME ABLATION: Active Causal Ablations for Selection Concentration")
    print("=" * 70)
    print("Conditions: NAME_ANON | NAME_ONLY | DESC_ONLY")
    print(f"Model: openai-codex/gpt-5.4-mini")
    print(f"Clusters: {len(ALL_CLUSTERS)}")
    print(f"Estimated calls: {len(ALL_CLUSTERS) * 3 * 25}")
    print()

    all_results = []

    for cluster_name in ALL_CLUSTERS:
        if cluster_name not in clusters:
            continue
        skills = clusters[cluster_name]["skills"]
        
        print(f"\n{'='*50}")
        print(f"Cluster: {cluster_name} ({len(skills)} skills)")
        print(f"  Original names: {[s['name'] for s in skills]}")
        print(f"{'='*50}")

        # Condition 1: NAME_ANON
        anon_skills = make_name_anon(skills)
        r1 = run_condition(f"NAME_ANON_{MODEL_TAG}", cluster_name, anon_skills, "NAME_ANON")
        all_results.append(r1)

        # Condition 2: NAME_ONLY
        nameonly_skills = make_name_only(skills)
        r2 = run_condition(f"NAME_ONLY_{MODEL_TAG}", cluster_name, nameonly_skills, "NAME_ONLY")
        all_results.append(r2)

        # Condition 3: DESC_ONLY (= NAME_ANON, but tracked separately for analysis)
        desconly_skills = make_desc_only(skills)
        r3 = run_condition(f"DESC_ONLY_{MODEL_TAG}", cluster_name, desconly_skills, "DESC_ONLY")
        all_results.append(r3)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY: Name Ablation Results")
    print("=" * 70)
    print(f"{'Cluster':20} {'NAME_ANON':12} {'NAME_ONLY':12} {'DESC_ONLY':12}")
    print("-" * 56)
    
    for cluster_name in ALL_CLUSTERS:
        vals = {}
        for r in all_results:
            if r.get("cluster") == cluster_name:
                vals[r["condition"]] = r["delta_api"]
        if vals:
            na = vals.get("NAME_ANON", 0)
            no = vals.get("NAME_ONLY", 0)
            do = vals.get("DESC_ONLY", 0)
            print(f"{cluster_name:20} δ={na:.3f}      δ={no:.3f}      δ={do:.3f}")

    # Interpretation
    print("\n" + "=" * 70)
    print("INTERPRETATION")
    print("=" * 70)
    
    name_effects = []
    desc_effects = []
    for cluster_name in ALL_CLUSTERS:
        vals = {}
        for r in all_results:
            if r.get("cluster") == cluster_name:
                vals[r["condition"]] = r["delta_api"]
        if "NAME_ANON" in vals and "NAME_ONLY" in vals:
            # If NAME_ANON (generic names, real desc) still shows concentration
            # → description drives selection
            # If NAME_ONLY (real names, uniform desc) still shows concentration
            # → name drives selection
            na = vals["NAME_ANON"]
            no = vals["NAME_ONLY"]
            
            if na > 0.3:
                desc_effects.append(cluster_name)
            if no > 0.3:
                name_effects.append(cluster_name)
    
    print(f"Description-driven clusters (NAME_ANON δ>0.3): {len(desc_effects)}/8")
    print(f"  → {desc_effects}")
    print(f"Name-driven clusters (NAME_ONLY δ>0.3): {len(name_effects)}/8")
    print(f"  → {name_effects}")
    print()
    
    if len(desc_effects) > len(name_effects):
        print("✅ CONCLUSION: Description content is the PRIMARY driver of selection.")
    elif len(name_effects) > len(desc_effects):
        print("⚠️ CONCLUSION: Tool names contribute significantly to selection.")
    else:
        print("🔄 CONCLUSION: Both name and description contribute to selection.")

    print(f"\nTotal experiments: {len(all_results)}")
    print(f"Total calls: {sum(r.get('n_success',0) for r in all_results)}")


if __name__ == "__main__":
    main()
