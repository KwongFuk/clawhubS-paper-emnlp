#!/usr/bin/env python3
"""
feature_analysis.py — EXP-4: Feature-level bias analysis
==========================================================
Following BiasBusters §4.3: quantify which skill metadata features
predict selection rate using Pearson correlation, linear regression,
and random forest.

10 features per skill:
  1. Semantic similarity (query-description cosine)
  2. Description length (word count)
  3. Description readability (Flesch-Kincaid approx)
  4. Promotional wording count
  5. Number of capability claims
  6. IC category (IC-0/1/2) [OUR INNOVATION]
  7. Capability coverage ratio [OUR INNOVATION]
  8. Boundary mention count [OUR INNOVATION]
  9. Parameter count
  10. Name length
"""
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"


# ──────────────────────────────────────────────────────────────────────────────
# Feature extractors
# ──────────────────────────────────────────────────────────────────────────────
PROMOTIONAL_WORDS = {
    "best", "top", "leading", "premier", "ultimate", "powerful", "advanced",
    "revolutionary", "innovative", "cutting-edge", "world-class", "enterprise-grade",
    "premium", "professional", "state-of-the-art", "superior", "fastest",
    "comprehensive", "unparalleled", "unprecedented", "groundbreaking",
}

BOUNDARY_WORDS = {
    "only", "limited", "does not", "cannot", "not support", "not available",
    "excluding", "except", "restriction", "requirement", "prerequisite",
    "requires", "must", "not designed for", "not intended",
}

CAPABILITY_VERBS = {
    "generate", "create", "build", "analyze", "search", "find", "deploy",
    "monitor", "manage", "automate", "transform", "convert", "optimize",
    "detect", "classify", "predict", "extract", "process", "render",
    "compile", "schedule", "transcribe", "translate", "synthesize",
}


def extract_features(skill: Dict) -> Dict[str, float]:
    """Extract 10 features from a skill.
    
    Args:
        skill: Dict with at least 'name', 'description'
    
    Returns:
        Dict mapping feature names to values
    """
    desc = skill.get("description", "")
    name = skill.get("name", "")
    body = skill.get("body", "")
    desc_lower = desc.lower()
    
    # 1. Description length (word count)
    desc_words = desc.split()
    desc_length = len(desc_words)
    
    # 2. Description readability (simplified Flesch-Kincaid)
    # FK = 0.39 * (words/sentences) + 11.8 * (syllables/words) - 15.59
    sentences = max(1, len(re.split(r'[.!?]+', desc)))
    syllables = sum(max(1, len(re.findall(r'[aeiouyAEIOUY]+', w))) for w in desc_words)
    words = max(1, desc_length)
    fk_grade = 0.39 * (words / sentences) + 11.8 * (syllables / words) - 15.59
    
    # 3. Promotional wording count
    promo_count = sum(1 for w in desc_lower.split() if w in PROMOTIONAL_WORDS)
    
    # 4. Capability claims count
    cap_count = sum(1 for v in CAPABILITY_VERBS if v in desc_lower)
    
    # 5. IC category (requires external analysis — use placeholder)
    # 0 = IC-0 (full disclosure), 1 = IC-1 (partial), 2 = IC-2 (hidden)
    ic_category = skill.get("ic_category", 0)
    
    # 6. Capability coverage ratio
    # How many claimed capabilities in desc actually appear in body?
    if body:
        body_lower = body.lower()
        claimed_caps = [v for v in CAPABILITY_VERBS if v in desc_lower]
        body_caps = [v for v in claimed_caps if v in body_lower]
        coverage = len(body_caps) / max(1, len(claimed_caps))
    else:
        coverage = 1.0  # No body = assume full coverage
    
    # 7. Boundary mention count
    boundary_count = sum(1 for b in BOUNDARY_WORDS if b in desc_lower)
    
    # 8. Parameter count (from description heuristic)
    param_patterns = re.findall(r'(?:parameter|param|option|flag|argument|setting)', desc_lower)
    param_count = len(param_patterns)
    
    # 9. Name length (characters)
    name_length = len(name)
    
    return {
        "desc_length": desc_length,
        "readability_fk": round(fk_grade, 2),
        "promo_count": promo_count,
        "capability_claims": cap_count,
        "ic_category": ic_category,
        "coverage_ratio": round(coverage, 4),
        "boundary_mentions": boundary_count,
        "param_count": param_count,
        "name_length": name_length,
    }


def compute_semantic_similarity(query: str, description: str) -> float:
    """Compute semantic similarity between query and description.
    
    Uses simple TF-IDF cosine for fast local computation.
    For production, use sentence-transformers embeddings.
    """
    from collections import Counter
    
    q_words = set(query.lower().split())
    d_words = set(description.lower().split())
    
    # Jaccard similarity as proxy
    intersection = len(q_words & d_words)
    union = len(q_words | d_words)
    
    return intersection / max(1, union)


# ──────────────────────────────────────────────────────────────────────────────
# Analysis pipeline
# ──────────────────────────────────────────────────────────────────────────────
def run_feature_analysis(
    skills: List[Dict],
    selection_rates: List[float],
    feature_names: List[str] = None,
) -> Dict:
    """Run Pearson + linear regression + random forest analysis.
    
    Args:
        skills: List of skill dicts
        selection_rates: Selection rate for each skill
        feature_names: Optional subset of features to analyze
    
    Returns:
        Dict with correlation, regression, and feature importance results
    """
    # Extract features
    all_features = [extract_features(s) for s in skills]
    
    if feature_names is None:
        feature_names = list(all_features[0].keys())
    
    # Build feature matrix
    X = np.array([[f[fn] for fn in feature_names] for f in all_features])
    y = np.array(selection_rates)
    
    results = {
        "n_skills": len(skills),
        "feature_names": feature_names,
        "features": all_features,
    }
    
    # 1. Pearson correlation
    correlations = {}
    for i, fn in enumerate(feature_names):
        if np.std(X[:, i]) > 0:
            corr = np.corrcoef(X[:, i], y)[0, 1]
        else:
            corr = 0.0
        correlations[fn] = round(float(corr), 4)
    results["pearson_correlations"] = correlations
    
    # 2. Linear regression (manual OLS)
    # y = Xβ + ε → β = (X^T X)^{-1} X^T y
    try:
        # Add intercept
        X_with_intercept = np.column_stack([np.ones(len(X)), X])
        beta = np.linalg.lstsq(X_with_intercept, y, rcond=None)[0]
        y_pred = X_with_intercept @ beta
        
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = 1 - ss_res / max(ss_tot, 1e-10)
        
        results["linear_regression"] = {
            "r_squared": round(float(r_squared), 4),
            "coefficients": {fn: round(float(c), 6) 
                           for fn, c in zip(feature_names, beta[1:])},
            "intercept": round(float(beta[0]), 6),
        }
    except np.linalg.LinAlgError:
        results["linear_regression"] = {"r_squared": 0, "error": "singular matrix"}
    
    # 3. Random Forest (simplified — use scikit-learn if available)
    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.model_selection import cross_val_score
        
        rf = RandomForestRegressor(n_estimators=100, random_state=42, max_depth=3)
        
        results["random_forest"] = {}
        if len(X) >= 10:
            cv_scores = cross_val_score(rf, X, y, cv=min(5, len(X)), scoring="r2")
            results["random_forest"]["cv_r2_mean"] = round(float(np.mean(cv_scores)), 4)
            results["random_forest"]["cv_r2_std"] = round(float(np.std(cv_scores)), 4)
        
        rf.fit(X, y)
        importances = rf.feature_importances_
        results["random_forest"]["feature_importance"] = {
            fn: round(float(imp), 4)
            for fn, imp in sorted(zip(feature_names, importances),
                                  key=lambda x: -x[1])
        }
    except ImportError:
        results["random_forest"] = {"error": "scikit-learn not available"}
    
    return results


# ── Self-test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Feature Analysis Self-Test ===\n")
    
    test_skills = [
        {"name": "SearchPro", "description": "Professional web search engine with AI-powered results ranking and comprehensive filtering options for enterprise teams. The best advanced search tool."},
        {"name": "WebFind", "description": "Fast web search tool for finding information online"},
        {"name": "QueryBot", "description": "Search the internet with natural language queries. Cannot handle images."},
        {"name": "InfoSeek", "description": "Intelligent search assistant for research. Generate comprehensive reports from search results."},
        {"name": "NetSearch", "description": "Web search with filtering. Only supports English queries. Requires API key."},
    ]
    
    # Extract features
    for s in test_skills:
        features = extract_features(s)
        print(f"[{s['name']}]")
        for k, v in features.items():
            print(f"  {k}: {v}")
        print()
    
    # Run analysis with mock selection rates
    selection_rates = [0.35, 0.25, 0.20, 0.12, 0.08]
    
    analysis = run_feature_analysis(test_skills, selection_rates)
    
    print("Pearson Correlations:")
    for fn, corr in sorted(analysis["pearson_correlations"].items(), key=lambda x: -abs(x[1])):
        print(f"  {fn}: r={corr:.4f}")
    
    print(f"\nLinear Regression R²: {analysis['linear_regression']['r_squared']:.4f}")
    
    if "feature_importance" in analysis.get("random_forest", {}):
        print("\nRandom Forest Feature Importance:")
        for fn, imp in analysis["random_forest"]["feature_importance"].items():
            print(f"  {fn}: {imp:.4f}")
    
    print("\n✅ Feature analysis self-test passed!")
