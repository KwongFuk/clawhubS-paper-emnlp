#!/usr/bin/env python3
"""
IC Class × Selection Rate Cross-Analysis + Description Repair Agent
Maps the 40 benchmark tools to their IC classes and analyzes correlation.
Then generates repair sentences for IC-1s/1q tools.
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict

DB_PATH = os.path.expanduser("~/codex/clawhubS/data/experiment1_split/merged.sqlite3")
CLUSTERS_PATH = os.path.expanduser("~/codex/clawhubS/skill-X/experiments/bias_benchmark/clusters_final.json")
RESULTS_DIR = os.path.expanduser("~/codex/clawhubS/skill-X/experiments/bias_benchmark/results")
OUT_DIR = os.path.expanduser("~/codex/clawhubS/paper-emnlp/analysis_output")
os.makedirs(OUT_DIR, exist_ok=True)

# ============================================================
# Part 1: Load clusters and selection results
# ============================================================
print("=" * 60)
print("Loading cluster tools and selection results")
print("=" * 60)

with open(CLUSTERS_PATH) as f:
    clusters = json.load(f)

summary_path = os.path.join(RESULTS_DIR, "exp1_gemini-3.1-pro_run1_summary.json")
with open(summary_path) as f:
    results = json.load(f)

# Build tool → (cluster, selection_rate, description) mapping
tool_info = {}
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

for cluster_name, cluster_data in clusters.items():
    skills = cluster_data.get("skills", [])
    result_cluster = results.get("clusters", {}).get(cluster_name, {})
    sel_dist = result_cluster.get("selection_dist", {})
    
    for skill in skills:
        sid = skill["skill_id"]
        name = skill["name"]
        desc = skill.get("description", "")
        
        # Get selection rate
        sel_rate = sel_dist.get(name, 0.0)
        
        # Get description from DB
        cur.execute("SELECT description FROM skills WHERE skill_id=?", (sid,))
        row = cur.fetchone()
        db_desc = row[0] if row else desc
        
        tool_info[sid] = {
            "name": name,
            "cluster": cluster_name,
            "selection_rate": sel_rate,
            "description": db_desc or desc or "",
            "desc_len": len(db_desc or desc or ""),
        }

print(f"Loaded {len(tool_info)} benchmark tools across {len(clusters)} clusters")

# ============================================================
# Part 2: Description Quality Metrics for benchmark tools
# ============================================================
print("\n" + "=" * 60)
print("Part 2: Description Quality × Selection Rate")
print("=" * 60)

# Compute metrics
def specificity_score(desc):
    """Count concrete indicators: numbers, version strings, units."""
    import re
    numbers = len(re.findall(r'\d+', desc))
    versions = len(re.findall(r'v\d', desc, re.I))
    units = len(re.findall(r'(ms|sec|MB|GB|API|HTTP|REST|JSON|XML)', desc))
    return numbers + versions + units

def promotional_count(desc):
    """Count promotional/marketing words."""
    promos = ['best', 'powerful', 'advanced', 'ultimate', 'revolutionary', 
              'cutting-edge', 'state-of-the-art', 'seamless', 'blazing']
    return sum(1 for p in promos if p.lower() in desc.lower())

def flesch_kincaid(text):
    if not text or len(text.strip()) < 10:
        return 0
    words = text.split()
    n_words = len(words)
    if n_words == 0:
        return 0
    n_sentences = max(1, text.count('.') + text.count('!') + text.count('?'))
    n_syllables = sum(max(1, len([c for c in w if c in 'aeiouAEIOU'])) for w in words)
    return 0.39 * (n_words / n_sentences) + 11.8 * (n_syllables / n_words) - 15.59

print(f"\n{'Name':<25} {'Cluster':<18} {'Sel%':>5} {'Len':>5} {'Spec':>5} {'FK':>6} {'Promo':>5}")
print("-" * 90)

selected_tools = []
excluded_tools = []

for sid, info in sorted(tool_info.items(), key=lambda x: -x[1]["selection_rate"]):
    desc = info["description"]
    spec = specificity_score(desc)
    fk = flesch_kincaid(desc)
    promo = promotional_count(desc)
    
    info["specificity"] = spec
    info["fk_score"] = fk
    info["promo_count"] = promo
    
    sel_pct = info["selection_rate"] * 100
    print(f"{info['name'][:24]:<25} {info['cluster'][:17]:<18} {sel_pct:>4.0f}% {info['desc_len']:>5} {spec:>5} {fk:>6.1f} {promo:>5}")
    
    if info["selection_rate"] > 0:
        selected_tools.append(info)
    else:
        excluded_tools.append(info)

# Correlation analysis
print(f"\n{'Metric':<20} {'Selected (n={})'.format(len(selected_tools)):>20} {'Excluded (n={})'.format(len(excluded_tools)):>20} {'Diff':>10}")
print("-" * 75)

for metric in ["desc_len", "specificity", "fk_score", "promo_count"]:
    sel_vals = [t[metric] for t in selected_tools]
    exc_vals = [t[metric] for t in excluded_tools]
    sel_mean = sum(sel_vals) / max(1, len(sel_vals))
    exc_mean = sum(exc_vals) / max(1, len(exc_vals))
    diff = sel_mean - exc_mean
    print(f"{metric:<20} {sel_mean:>20.1f} {exc_mean:>20.1f} {diff:>+10.1f}")

# ============================================================
# Part 3: Description Repair Agent — generate repairs
# ============================================================
print("\n" + "=" * 60)
print("Part 3: Description Repair Agent — Candidate Generation")
print("=" * 60)

# For each tool with 0% selection rate, identify description weaknesses
# and generate a repair suggestion
print("\nTools with 0% selection rate (repair candidates):")
repair_candidates = []

for info in excluded_tools:
    weaknesses = []
    if info["desc_len"] < 80:
        weaknesses.append("too_short")
    if info["specificity"] == 0:
        weaknesses.append("no_specifics")
    if info["fk_score"] > 12:
        weaknesses.append("too_complex")
    if info["promo_count"] > 0:
        weaknesses.append("promotional")
    if "self-hosted" in info["description"].lower() or "privacy" in info["description"].lower():
        weaknesses.append("non_functional_framing")
    
    repair_candidates.append({
        "name": info["name"],
        "cluster": info["cluster"],
        "description": info["description"],
        "desc_len": info["desc_len"],
        "weaknesses": weaknesses,
    })
    
    print(f"\n  {info['name']} ({info['cluster']})")
    print(f"    Desc ({info['desc_len']} chars): {info['description'][:120]}")
    print(f"    Weaknesses: {', '.join(weaknesses) if weaknesses else 'none identified'}")

# Save repair candidates
with open(os.path.join(OUT_DIR, "repair_candidates.json"), 'w') as f:
    json.dump(repair_candidates, f, indent=2)

# ============================================================
# Part 4: Generate repair prompts for LLM
# ============================================================
print("\n" + "=" * 60)
print("Part 4: Repair Prompt Templates")
print("=" * 60)

repair_prompts = []
for cand in repair_candidates:
    cluster_tools = [t for t in tool_info.values() if t["cluster"] == cand["cluster"]]
    top_tool = max(cluster_tools, key=lambda t: t["selection_rate"])
    
    prompt = f"""You are a tool marketplace description improvement agent.

TASK: Improve the description of "{cand['name']}" to better communicate its capabilities.

CURRENT DESCRIPTION: {cand['description']}

REFERENCE (top-selected tool in same category): 
  Name: {top_tool['name']}
  Description: {top_tool['description'][:200]}

WEAKNESSES IDENTIFIED: {', '.join(cand['weaknesses'])}

GUIDELINES:
1. Add concrete capability details (what APIs, formats, speeds)
2. Use simple, direct language (target FK grade 8-10)
3. Avoid promotional words (best, powerful, advanced)
4. Keep under 200 characters
5. Focus on WHAT it does, not WHO made it

OUTPUT: A single improved description sentence."""
    
    repair_prompts.append({
        "tool_name": cand["name"],
        "cluster": cand["cluster"],
        "prompt": prompt,
    })
    print(f"\n--- Repair for: {cand['name']} ---")
    print(f"  Weaknesses: {cand['weaknesses']}")
    print(f"  Reference: {top_tool['name']} (sel={top_tool['selection_rate']*100:.0f}%)")

with open(os.path.join(OUT_DIR, "repair_prompts.json"), 'w') as f:
    json.dump(repair_prompts, f, indent=2)

# ============================================================
# Part 5: Summary Statistics
# ============================================================
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

print(f"Total benchmark tools: {len(tool_info)}")
print(f"Selected (>0% rate): {len(selected_tools)}")
print(f"Never selected (0%): {len(excluded_tools)}")
print(f"Repair candidates: {len(repair_candidates)}")

# Per-cluster summary
print("\nPer-cluster selection concentration:")
for cluster_name in clusters:
    cluster_tools = [t for t in tool_info.values() if t["cluster"] == cluster_name]
    if not cluster_tools:
        continue
    n_selected = sum(1 for t in cluster_tools if t["selection_rate"] > 0)
    top = max(cluster_tools, key=lambda t: t["selection_rate"])
    print(f"  {cluster_name:<20} {n_selected}/{len(cluster_tools)} selected, top={top['name']} ({top['selection_rate']*100:.0f}%)")

conn.close()

print(f"\nResults saved to {OUT_DIR}/")
print("Done!")
