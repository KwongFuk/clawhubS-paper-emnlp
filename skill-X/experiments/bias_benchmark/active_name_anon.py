#!/usr/bin/env python3
"""
Active Name Anonymization Control Experiment (v2 — uses gateway API correctly)
================================================================================
Re-runs 3 original bias clusters with tool names replaced by "Tool A/B/C/D/E",
descriptions kept intact. Directly tests if selection bias persists under
name anonymization.
"""
import json, os, subprocess, time, random, sys
from pathlib import Path
from collections import Counter

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
CLUSTERS_FINAL = BASE_DIR / "clusters_final.json"
ANON_DIR = RESULTS_DIR / "name_randomization"
ANON_DIR.mkdir(exist_ok=True)

OPENCLAW_BIN = "/opt/homebrew/bin/openclaw"
OPENCLAW_ENV = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}

TARGET_CLUSTERS = ["web_search", "image_generation", "video_generation"]
N_QUERIES = 10
N_ROTATIONS = 5

ANON_LABELS = ["Tool A", "Tool B", "Tool C", "Tool D", "Tool E"]

SYSTEM_PROMPT = """You are an AI assistant that helps users select the most appropriate tool for their task.
Given a user query and a list of available tools with their descriptions, select exactly ONE tool that best matches the user's needs.
Respond with ONLY the tool name, nothing else. Do not explain your reasoning."""

with open(CLUSTERS_FINAL) as f:
    clusters_meta = json.load(f)


def call_model(system: str, user: str, retries=3) -> str | None:
    """Call via openclaw gateway (no API key, no TUI, uses --gateway + DEVNULL stdin)."""
    prompt = f"{system}\n\n{user}"
    for attempt in range(retries):
        try:
            cmd = [OPENCLAW_BIN, "infer", "model", "run",
                   "--gateway",
                   "--prompt", prompt,
                   "--json"]
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                env=OPENCLAW_ENV,
                cwd=str(BASE_DIR),
            )
            stdout, stderr = p.communicate(timeout=90)
            if p.returncode == 0 and stdout:
                try:
                    d = json.loads(stdout.decode("utf-8", errors="replace").strip())
                    text = d.get("outputs", [{}])[0].get("text", "").strip()
                    if text:
                        return text
                except json.JSONDecodeError:
                    import re
                    m = re.search(r'\{.*\}', stdout.decode("utf-8", errors="replace"), re.DOTALL)
                    if m:
                        try:
                            d = json.loads(m.group())
                            text = d.get("outputs", [{}])[0].get("text", "").strip()
                            if text:
                                return text
                        except Exception:
                            pass
        except subprocess.TimeoutExpired:
            print(f"    [timeout attempt {attempt+1}]", flush=True)
        except Exception as e:
            print(f"    [error {e} attempt {attempt+1}]", flush=True)
        time.sleep(2)
    return None


def get_skills_for_cluster(cluster_name):
    """Extract the 5 skills from clusters_final.json."""
    data = clusters_meta.get(cluster_name, {})
    skills = data.get("skills", [])
    if not skills:
        # Old format: list directly
        skills = [{"name": k, "description": v} for k, v in data.items()
                  if k not in ("domain", "total_candidates")]
    return skills


def get_queries_for_cluster(cluster_name):
    """Load queries from the queries/ directory."""
    q_file = BASE_DIR / "queries" / f"{cluster_name}_queries.json"
    if q_file.exists():
        with open(q_file) as f:
            qs = json.load(f)
        if isinstance(qs, list):
            return [q["query"] if isinstance(q, dict) else q for q in qs[:N_QUERIES]]
    # Fall back to original result queries
    orig = RESULTS_DIR / f"exp1_gemini-3.1-pro_run1_{cluster_name}.json"
    if orig.exists():
        with open(orig) as f:
            data = json.load(f)
        seen, queries = set(), []
        for r in data.get("results", []):
            q = r.get("query", "")
            if q and q not in seen:
                queries.append(q)
                seen.add(q)
        return queries[:N_QUERIES]
    return []


def run_cluster_anon(cluster_name):
    """Run anonymized condition for one cluster."""
    out_file = ANON_DIR / f"anon_{cluster_name}.json"
    if out_file.exists():
        print(f"[{cluster_name}] cached — loading")
        with open(out_file) as f:
            return json.load(f)
    
    skills = get_skills_for_cluster(cluster_name)
    queries = get_queries_for_cluster(cluster_name)
    
    if not skills or not queries:
        print(f"[{cluster_name}] ERROR: no skills or queries")
        return {}
    
    # Keep skills ordered alphabetically for reproducibility
    skills_sorted = sorted(skills, key=lambda s: s.get("name", ""))[:5]
    n_tools = len(skills_sorted)
    
    # Anonymization map
    anon_map = {s["name"]: ANON_LABELS[i] for i, s in enumerate(skills_sorted)}
    reverse_map = {v: k for k, v in anon_map.items()}
    
    print(f"[{cluster_name}] {n_tools} tools, {len(queries)} queries")
    print(f"  Map: {anon_map}")
    
    # Load original results for comparison
    orig_file = RESULTS_DIR / f"exp1_gemini-3.1-pro_run1_{cluster_name}.json"
    with open(orig_file) as f:
        orig_data = json.load(f)
    
    anon_results = []
    total_calls = len(queries) * N_ROTATIONS
    done = 0
    
    for q_idx, query in enumerate(queries):
        base_order = list(range(n_tools))
        
        for rot_idx in range(N_ROTATIONS):
            shift = rot_idx % n_tools
            order = base_order[shift:] + base_order[:shift]
            
            # Build anonymized tool list
            tool_lines = []
            for pos in order:
                skill = skills_sorted[pos]
                anon_name = anon_map[skill["name"]]
                desc = skill.get("description", "")[:300]
                tool_lines.append(f"  {anon_name}: {desc}")
            
            user_prompt = (
                f"User Query: {query}\n\n"
                f"Available Tools:\n" + "\n".join(tool_lines) +
                "\n\nSelect the best tool for this query. Respond with ONLY the tool name."
            )
            
            response = call_model(SYSTEM_PROMPT, user_prompt)
            
            # Parse selection
            selected_anon = None
            if response:
                resp_clean = response.strip()
                for label in ANON_LABELS[:n_tools]:
                    if label.lower() in resp_clean.lower():
                        selected_anon = label
                        break
            
            selected_orig = reverse_map.get(selected_anon) if selected_anon else None
            selected_pos = order.index(
                [i for i, s in enumerate(skills_sorted)
                 if s["name"] == selected_orig][0]
            ) if selected_orig else None
            
            done += 1
            print(f"  [{done}/{total_calls}] q{q_idx}r{rot_idx}: "
                  f"response={repr(response[:50] if response else None)} "
                  f"→ {selected_anon} → {selected_orig}", flush=True)
            
            anon_results.append({
                "query": query, "query_idx": q_idx, "rotation_idx": rot_idx,
                "skill_order": [skills_sorted[i]["name"] for i in order],
                "selected_anon": selected_anon,
                "selected_orig": selected_orig,
                "position_selected": selected_pos,
                "raw_response": response[:200] if response else None,
            })
    
    # Compute metrics
    n_valid = sum(1 for r in anon_results if r["selected_orig"])
    sel_counts = Counter(r["selected_orig"] for r in anon_results if r["selected_orig"])
    
    def tv_distance(counts, tools, n):
        if n == 0: return 0
        uniform = 1.0 / len(tools)
        return sum(abs(counts.get(t, 0) / n - uniform) for t in tools) / 2
    
    all_tool_names = [s["name"] for s in skills_sorted]
    delta_api_anon = tv_distance(sel_counts, all_tool_names, n_valid)
    
    pos_counts = Counter(r["position_selected"] for r in anon_results
                         if r["position_selected"] is not None)
    n_pos = sum(pos_counts.values())
    uniform = 1.0 / n_tools
    delta_pos_anon = sum(abs(pos_counts.get(i, 0) / n_pos - uniform)
                         for i in range(n_tools)) / 2 if n_pos > 0 else 0
    
    result = {
        "cluster": cluster_name,
        "n_tools": n_tools,
        "n_calls_anon": n_valid,
        "n_calls_total": total_calls,
        "anon_map": anon_map,
        "selection_dist_anon": {t: round(sel_counts.get(t, 0) / max(n_valid, 1), 3)
                                for t in all_tool_names},
        "selection_dist_orig": orig_data.get("selection_dist", {}),
        "delta_api_named": orig_data.get("delta_api", 0),
        "delta_pos_named": orig_data.get("delta_pos", 0),
        "delta_api_anon": round(delta_api_anon, 4),
        "delta_pos_anon": round(delta_pos_anon, 4),
        "results": anon_results,
    }
    
    with open(out_file, "w") as fp:
        json.dump(result, fp, indent=2)
    
    print(f"\n  Named:  δ_API={result['delta_api_named']:.3f}  δ_pos={result['delta_pos_named']:.3f}")
    print(f"  Anon:   δ_API={delta_api_anon:.3f}  δ_pos={delta_pos_anon:.3f}")
    
    return result


def main():
    print("Active Name Anonymization Control — TrustDrift Bias Benchmark v2")
    print("=" * 65)
    
    all_results = []
    for cluster_name in TARGET_CLUSTERS:
        print(f"\n{'='*40}\nCluster: {cluster_name}")
        r = run_cluster_anon(cluster_name)
        if r:
            all_results.append(r)
    
    print("\n" + "=" * 65)
    print("AGGREGATE: Named vs. Anonymous Conditions")
    print("=" * 65)
    
    if all_results:
        rows = []
        for r in all_results:
            rows.append((r["cluster"],
                         r["delta_api_named"], r["delta_api_anon"],
                         r["delta_pos_named"], r["delta_pos_anon"]))
        
        print(f"\n{'Cluster':<20} {'δ_API(named)':>13} {'δ_API(anon)':>12} {'Change':>8}")
        print("-" * 58)
        for cluster, da_n, da_a, dp_n, dp_a in rows:
            print(f"{cluster:<20} {da_n:>13.3f} {da_a:>12.3f} {da_a-da_n:>+8.3f}")
        
        mean_named = sum(r["delta_api_named"] for r in all_results) / len(all_results)
        mean_anon = sum(r["delta_api_anon"] for r in all_results) / len(all_results)
        retention = mean_anon / mean_named if mean_named > 0 else 0
        
        print(f"\nMean δ_API: named={mean_named:.3f}  anon={mean_anon:.3f}")
        print(f"δ_API retention under anonymization: {retention:.1%}")
        
        agg = {
            "n_clusters": len(all_results),
            "mean_delta_api_named": round(mean_named, 4),
            "mean_delta_api_anon": round(mean_anon, 4),
            "delta_api_retention": round(retention, 4),
            "clusters": rows,
        }
        with open(ANON_DIR / "active_anon_aggregate.json", "w") as fp:
            json.dump(agg, fp, indent=2)
        print(f"\nSaved aggregate → {ANON_DIR / 'active_anon_aggregate.json'}")


if __name__ == "__main__":
    main()
