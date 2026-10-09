"""
bias_metrics.py — Formal bias metrics following BiasBusters (ICLR 2026)
=========================================================================
Implements:
  δ_API  — API-level total variation distance from uniform
  δ_pos  — Position-level total variation distance from uniform
  δ_model — Combined model-level bias metric
"""
import numpy as np
from collections import Counter
from typing import Dict, List, Tuple


def total_variation_distance(p: np.ndarray, q: np.ndarray) -> float:
    """Total variation distance between two distributions.
    
    TV(p, q) = 0.5 * sum(|p_i - q_i|)
    
    Range: [0, 1] where 0 = identical, 1 = no overlap.
    """
    return 0.5 * np.sum(np.abs(p - q))


def compute_delta_api(selections: List[str], skill_names: List[str]) -> float:
    """Compute δ_API: API-level bias as TV distance from uniform.
    
    Args:
        selections: List of selected skill names across all queries and rotations
        skill_names: List of all skill names in the cluster
    
    Returns:
        δ_API value in [0, 1]
    """
    K = len(skill_names)
    counts = Counter(selections)
    total = sum(counts.values())
    
    if total == 0:
        return 0.0
    
    # Empirical distribution
    p_api = np.array([counts.get(name, 0) / total for name in skill_names])
    # Uniform distribution
    uniform = np.ones(K) / K
    
    return total_variation_distance(p_api, uniform)


def compute_delta_pos(selections_by_position: Dict[int, int],
                      K: int) -> float:
    """Compute δ_pos: Position-level bias as TV distance from uniform.
    
    Args:
        selections_by_position: {position: count_selected} where position is 0-indexed
        K: Number of positions (= number of skills in cluster)
    
    Returns:
        δ_pos value in [0, 1]
    """
    total = sum(selections_by_position.values())
    if total == 0:
        return 0.0
    
    # Empirical position distribution
    p_pos = np.array([selections_by_position.get(i, 0) / total for i in range(K)])
    # Uniform distribution
    uniform = np.ones(K) / K
    
    return total_variation_distance(p_pos, uniform)


def compute_delta_model(delta_apis: List[float], 
                        delta_poss: List[float]) -> float:
    """Compute δ_model: Combined model-level bias averaged over clusters.
    
    δ_model = mean over clusters of (δ_API + δ_pos) / 2
    
    Args:
        delta_apis: List of δ_API values, one per cluster
        delta_poss: List of δ_pos values, one per cluster
    
    Returns:
        δ_model value in [0, 1]
    """
    assert len(delta_apis) == len(delta_poss)
    cluster_biases = [(a + p) / 2 for a, p in zip(delta_apis, delta_poss)]
    return float(np.mean(cluster_biases))


def cyclic_rotations(items: list, K: int = None) -> List[list]:
    """Generate K cyclic rotations of a list.
    
    Following BiasBusters: each rotation shifts the list by 1 position,
    ensuring every item appears at every position exactly once.
    
    Args:
        items: List to rotate
        K: Number of rotations (default = len(items))
    
    Returns:
        List of K rotated lists
    """
    n = len(items)
    K = K or n
    return [items[i:] + items[:i] for i in range(K)]


def analyze_cluster_results(results: List[Dict]) -> Dict:
    """Analyze results for a single cluster across all queries and rotations.
    
    Args:
        results: List of dicts with keys:
            - query: str
            - rotation_idx: int 
            - skill_order: List[str]  (skill names in presentation order)
            - selected: str  (selected skill name)
    
    Returns:
        Dict with:
            - delta_api: float
            - delta_pos: float
            - delta_cluster: float  (= (delta_api + delta_pos) / 2)
            - selection_dist: Dict[str, float]
            - position_dist: Dict[int, float]
            - n_queries: int
            - n_total: int
    """
    if not results:
        return {"delta_api": 0, "delta_pos": 0, "delta_cluster": 0}
    
    # Collect all unique skill names
    all_skills = set()
    for r in results:
        all_skills.update(r["skill_order"])
    skill_names = sorted(all_skills)
    K = len(skill_names)
    
    # Count selections per API
    api_selections = [r["selected"] for r in results]
    
    # Count selections per position
    pos_counts = Counter()
    for r in results:
        selected = r["selected"]
        order = r["skill_order"]
        if selected in order:
            pos = order.index(selected)
            pos_counts[pos] += 1
    
    delta_api = compute_delta_api(api_selections, skill_names)
    delta_pos = compute_delta_pos(dict(pos_counts), K)
    delta_cluster = (delta_api + delta_pos) / 2
    
    # Selection distribution
    total = len(api_selections)
    sel_counter = Counter(api_selections)
    sel_dist = {name: sel_counter.get(name, 0) / total for name in skill_names}
    
    # Position distribution  
    pos_total = sum(pos_counts.values())
    pos_dist = {i: pos_counts.get(i, 0) / pos_total if pos_total else 0 
                for i in range(K)}
    
    return {
        "delta_api": round(delta_api, 4),
        "delta_pos": round(delta_pos, 4),
        "delta_cluster": round(delta_cluster, 4),
        "selection_dist": sel_dist,
        "position_dist": pos_dist,
        "n_queries": len(set(r["query"] for r in results)),
        "n_total": total,
        "K": K,
    }


def cross_model_correlation(model_results: Dict[str, List[float]]) -> Dict:
    """Compute pairwise Pearson correlation between models' selection distributions.
    
    Following BiasBusters §4.2: concatenate each model's selection distributions
    across all clusters into a single vector, then compute pairwise correlations.
    
    Args:
        model_results: {model_name: [concatenated selection rates]}
    
    Returns:
        Dict with correlation matrix and model names
    """
    models = sorted(model_results.keys())
    n = len(models)
    corr_matrix = np.zeros((n, n))
    
    for i, m1 in enumerate(models):
        for j, m2 in enumerate(models):
            v1 = np.array(model_results[m1])
            v2 = np.array(model_results[m2])
            if len(v1) == len(v2) and len(v1) > 0:
                corr = np.corrcoef(v1, v2)[0, 1]
                corr_matrix[i, j] = corr
            else:
                corr_matrix[i, j] = float('nan')
    
    return {
        "models": models,
        "correlation_matrix": corr_matrix.tolist(),
    }


# ── Self-test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Bias Metrics Self-Test ===\n")
    
    # Test 1: Perfect uniform → δ=0
    skills = ["A", "B", "C", "D", "E"]
    selections = skills * 20  # 20 of each
    delta = compute_delta_api(selections, skills)
    print(f"Uniform selection → δ_API = {delta:.4f} (expected: 0.0000)")
    assert abs(delta) < 1e-10
    
    # Test 2: Complete fixation → δ close to 0.8 (= (1-1/5) * 0.5 * 5... = 0.8)
    selections = ["A"] * 100
    delta = compute_delta_api(selections, skills)
    print(f"Complete fixation → δ_API = {delta:.4f} (expected: 0.8000)")
    assert abs(delta - 0.8) < 1e-10
    
    # Test 3: Moderate bias
    selections = ["A"] * 40 + ["B"] * 30 + ["C"] * 15 + ["D"] * 10 + ["E"] * 5
    delta = compute_delta_api(selections, skills)
    print(f"Moderate bias     → δ_API = {delta:.4f} (expected: ~0.30)")
    
    # Test 4: Cyclic rotations
    rotations = cyclic_rotations(["X", "Y", "Z"])
    print(f"\nCyclic rotations of [X, Y, Z]:")
    for i, r in enumerate(rotations):
        print(f"  Rotation {i}: {r}")
    
    # Test 5: Cluster analysis
    results = []
    for q_idx in range(10):
        for rot_idx, order in enumerate(cyclic_rotations(skills)):
            # Simulate: always pick first item (pure primacy bias)
            results.append({
                "query": f"query_{q_idx}",
                "rotation_idx": rot_idx,
                "skill_order": order,
                "selected": order[0],
            })
    analysis = analyze_cluster_results(results)
    print(f"\nPure primacy bias (always pick first):")
    print(f"  δ_API = {analysis['delta_api']:.4f} (expected: 0.0000 due to rotations)")
    print(f"  δ_pos = {analysis['delta_pos']:.4f} (expected: 0.8000)")
    print(f"  δ_cluster = {analysis['delta_cluster']:.4f}")
    
    print("\n✅ All self-tests passed!")
