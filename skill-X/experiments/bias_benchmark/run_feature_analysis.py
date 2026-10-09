#!/usr/bin/env python3
"""
run_feature_analysis.py — EXP-4: Post-hoc feature analysis on EXP-1 results
=============================================================================
No API calls needed. Reads completed cluster results and clusters_final.json,
extracts features from each skill, and correlates with selection rates.

Outputs:
  - Pearson correlations table (paper Table 5)
  - Linear regression R² and coefficients
  - Random forest feature importance
  - Per-skill feature breakdown
"""
import json, sys
from pathlib import Path
from collections import Counter

import numpy as np

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
CLUSTERS_FILE = BASE_DIR / "clusters_final.json"

# Import from our modules
from feature_analysis import extract_features, run_feature_analysis, PROMOTIONAL_WORDS

# Additional promotional words observed in experiments
EXTRA_PROMO = {
    "optimized", "real-time", "ai-powered", "official", "integration",
    "privacy", "self-hosted", "automated", "smart", "intelligent",
    "high-quality", "production-ready", "scalable", "reliable",
}
ALL_PROMO = PROMOTIONAL_WORDS | EXTRA_PROMO


def load_exp1_data(model="gemini-3.1-pro", run_id=1):
    """Load EXP-1 results + cluster skill metadata."""
    clusters = json.loads(CLUSTERS_FILE.read_text())
    
    data = []  # List of (skill_dict, selection_rate, cluster_name)
    
    for cluster_name, cluster_info in clusters.items():
        result_file = RESULTS_DIR / f"exp1_{model}_run{run_id}_{cluster_name}.json"
        if not result_file.exists():
            continue
        
        result = json.loads(result_file.read_text())
        sel_dist = result.get("selection_dist", {})
        skills = cluster_info["skills"][:5]
        
        for skill in skills:
            rate = sel_dist.get(skill["name"], 0.0)
            data.append((skill, rate, cluster_name))
    
    return data


def enhanced_features(skill: dict) -> dict:
    """Extract features with additional promotional word detection."""
    base = extract_features(skill)
    desc_lower = skill.get("description", "").lower()
    
    # Enhanced promotional count
    promo_enhanced = sum(1 for w in ALL_PROMO if w in desc_lower)
    base["promo_enhanced"] = promo_enhanced
    
    # Has "LLM" or "AI" mention
    base["mentions_ai_llm"] = int("llm" in desc_lower or "ai" in desc_lower 
                                   or "artificial" in desc_lower)
    
    # Has authority signals
    authority = ["official", "verified", "certified", "trusted", "authorized"]
    base["authority_signals"] = sum(1 for a in authority if a in desc_lower)
    
    # Specificity score (numbers, versions, technical terms)
    base["specificity"] = len(re.findall(r'\d+\.?\d*', skill.get("description", "")))
    
    return base


import re

def main():
    data = load_exp1_data()
    
    if not data:
        print("No EXP-1 results found. Run run_all_clusters.py first.")
        sys.exit(1)
    
    skills = [d[0] for d in data]
    rates = [d[1] for d in data]
    cluster_names = [d[2] for d in data]
    
    n_clusters = len(set(cluster_names))
    print(f"Loaded {len(data)} skills from {n_clusters} clusters\n")
    
    # ── Per-skill feature table ──
    print("=" * 100)
    print("TABLE 5a: Per-Skill Features vs Selection Rate")
    print("=" * 100)
    print(f"{'Skill':<30s} {'Cluster':<20s} {'Rate':>6s} {'Desc↔':>6s} "
          f"{'Promo':>5s} {'AI/LLM':>6s} {'Auth':>5s} {'Caps':>5s} {'Bndry':>5s}")
    print("-" * 100)
    
    all_features = []
    for skill, rate, cluster in data:
        f = enhanced_features(skill)
        all_features.append(f)
        selected_marker = "★" if rate > 0.3 else ("·" if rate > 0 else " ")
        print(f"{selected_marker} {skill['name']:<28s} {cluster:<20s} {rate:>5.1%} "
              f"{f['desc_length']:>6d} {f['promo_enhanced']:>5d} "
              f"{f['mentions_ai_llm']:>6d} {f['authority_signals']:>5d} "
              f"{f['capability_claims']:>5d} {f['boundary_mentions']:>5d}")
    
    # ── Pearson correlations ──
    print("\n" + "=" * 80)
    print("TABLE 5b: Pearson Correlation (Feature vs Selection Rate)")
    print("=" * 80)
    
    feature_names = list(all_features[0].keys())
    X = np.array([[f[fn] for fn in feature_names] for f in all_features])
    y = np.array(rates)
    
    correlations = []
    for i, fn in enumerate(feature_names):
        if np.std(X[:, i]) > 0 and np.std(y) > 0:
            corr = np.corrcoef(X[:, i], y)[0, 1]
        else:
            corr = 0.0
        correlations.append((fn, corr))
    
    correlations.sort(key=lambda x: -abs(x[1]))
    
    print(f"{'Feature':<25s} {'Pearson r':>10s} {'Direction':>12s} {'Strength':>10s}")
    print("-" * 60)
    for fn, corr in correlations:
        strength = "STRONG" if abs(corr) > 0.5 else ("moderate" if abs(corr) > 0.3 else "weak")
        direction = "positive ↑" if corr > 0 else "negative ↓"
        marker = " *** " if abs(corr) > 0.5 else ""
        print(f"{fn:<25s} {corr:>+10.4f} {direction:>12s} {strength:>10s}{marker}")
    
    # ── Linear regression ──
    print("\n" + "=" * 80)
    print("LINEAR REGRESSION: Selection Rate ~ Features")
    print("=" * 80)
    
    try:
        X_with_intercept = np.column_stack([np.ones(len(X)), X])
        beta = np.linalg.lstsq(X_with_intercept, y, rcond=None)[0]
        y_pred = X_with_intercept @ beta
        
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = 1 - ss_res / max(ss_tot, 1e-10)
        
        print(f"R² = {r_squared:.4f}")
        print(f"\nCoefficients (sorted by |β|):")
        coefs = list(zip(feature_names, beta[1:]))
        coefs.sort(key=lambda x: -abs(x[1]))
        for fn, c in coefs:
            print(f"  {fn:<25s} β = {c:>+.6f}")
        print(f"  {'(intercept)':<25s} β = {beta[0]:>+.6f}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # ── Random forest ──
    print("\n" + "=" * 80)
    print("RANDOM FOREST: Feature Importance")
    print("=" * 80)
    
    try:
        from sklearn.ensemble import RandomForestRegressor
        
        rf = RandomForestRegressor(n_estimators=200, random_state=42, max_depth=3)
        rf.fit(X, y)
        
        importances = list(zip(feature_names, rf.feature_importances_))
        importances.sort(key=lambda x: -x[1])
        
        print(f"{'Feature':<25s} {'Importance':>12s} {'Bar':>30s}")
        print("-" * 70)
        for fn, imp in importances:
            bar = "█" * int(imp * 40)
            print(f"{fn:<25s} {imp:>12.4f} {bar}")
    except ImportError:
        print("  scikit-learn not available. Install with: pip install scikit-learn")
    
    # ── Key findings ──
    print("\n" + "=" * 80)
    print("KEY FINDINGS FOR PAPER")
    print("=" * 80)
    
    # Find top predictors
    top_corrs = [(fn, c) for fn, c in correlations if abs(c) > 0.3]
    if top_corrs:
        print(f"\n1. TOP PREDICTORS of selection (|r| > 0.3):")
        for fn, c in top_corrs:
            print(f"   - {fn}: r = {c:+.4f}")
    
    # Compare selected vs excluded
    selected_skills = [i for i, r in enumerate(rates) if r > 0]
    excluded_skills = [i for i, r in enumerate(rates) if r == 0]
    
    if selected_skills and excluded_skills:
        print(f"\n2. SELECTED vs EXCLUDED skill comparison:")
        for fn_idx, fn in enumerate(feature_names):
            sel_mean = np.mean([X[i, fn_idx] for i in selected_skills])
            exc_mean = np.mean([X[i, fn_idx] for i in excluded_skills])
            if abs(sel_mean - exc_mean) > 0.1 * max(abs(sel_mean), abs(exc_mean), 0.01):
                diff = sel_mean - exc_mean
                print(f"   {fn:<25s}: selected={sel_mean:.2f} vs excluded={exc_mean:.2f} "
                      f"(Δ={diff:+.2f})")
    
    # Save results
    output = {
        "n_skills": len(data),
        "n_clusters": n_clusters,
        "correlations": {fn: round(float(c), 4) for fn, c in correlations},
        "r_squared": round(float(r_squared), 4) if 'r_squared' in dir() else None,
        "per_skill": [
            {"name": skills[i]["name"], "cluster": cluster_names[i],
             "selection_rate": rates[i], "features": all_features[i]}
            for i in range(len(data))
        ],
    }
    
    out_file = RESULTS_DIR / "exp4_feature_analysis.json"
    out_file.write_text(json.dumps(output, indent=2))
    print(f"\n✅ Saved → {out_file}")


if __name__ == "__main__":
    main()
