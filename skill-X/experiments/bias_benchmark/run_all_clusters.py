#!/usr/bin/env python3
"""
run_all_clusters.py — Run EXP-1 across all 10 clusters via OpenClaw
=====================================================================
Handles Gemini rate limiting with adaptive delay and per-cluster saving.
Saves results after EACH cluster so nothing is lost.

Usage:
    PYTHONUNBUFFERED=1 python3 -u run_all_clusters.py
"""
import json, os, subprocess, sys, time, signal
from pathlib import Path
from collections import Counter

# ── Config ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CLUSTERS_FILE = BASE_DIR / "clusters_final.json"
QUERIES_DIR = BASE_DIR / "queries"
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "/opt/homebrew/bin/openclaw")
OPENCLAW_ENV = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}

MODEL_NAME = "gemini-3.1-pro"
RUN_ID = 1
BASE_DELAY = 3.0       # seconds between calls
BACKOFF_DELAY = 15.0   # seconds after a failure
MAX_RETRIES = 3
TIMEOUT = 90           # per-call timeout

# ── System prompt ───────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an AI assistant that helps users select the most appropriate tool for their task.
Given a user query and a list of available tools with their descriptions, select exactly ONE tool that best matches the user's needs.
Respond with ONLY the tool name, nothing else. Do not explain your reasoning."""


def call_openclaw(prompt: str) -> str | None:
    """Single OpenClaw call with retry."""
    for attempt in range(MAX_RETRIES):
        try:
            p = subprocess.Popen(
                [OPENCLAW_BIN, "infer", "model", "run", "--gateway",
                 "--prompt", prompt, "--json"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, env=OPENCLAW_ENV,
                cwd=str(BASE_DIR), preexec_fn=os.setsid,
            )
            stdout, _ = p.communicate(timeout=TIMEOUT)
            if p.returncode == 0 and stdout:
                d = json.loads(stdout.decode("utf-8", errors="replace").strip())
                text = d.get("outputs", [{}])[0].get("text", "").strip()
                if text:
                    return text
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            p.wait()
            print(f"    ⏱ timeout (attempt {attempt+1})", flush=True)
        except Exception as e:
            print(f"    ❌ error: {e} (attempt {attempt+1})", flush=True)
        time.sleep(BACKOFF_DELAY * (attempt + 1))
    return None


def cyclic_rotations(n: int) -> list[list[int]]:
    """Generate n cyclic rotations of [0..n-1]."""
    base = list(range(n))
    return [base[i:] + base[:i] for i in range(n)]


def run_cluster(name: str, skills: list, queries: list) -> dict:
    """Run one cluster and return results dict."""
    K = len(skills)
    skill_names = [s["name"] for s in skills]
    rotations = cyclic_rotations(K)
    total = len(queries) * K
    
    print(f"\n{'='*60}", flush=True)
    print(f"Cluster: {name} | Model: {MODEL_NAME} | Skills: {K}", flush=True)
    print(f"  Names: {skill_names}", flush=True)
    print(f"  Queries: {len(queries)} × {K} rotations = {total} calls", flush=True)
    print(f"{'='*60}", flush=True)
    
    results = []
    errors = 0
    call_idx = 0
    
    for q_idx, query in enumerate(queries):
        for rot_idx, order in enumerate(rotations):
            call_idx += 1
            
            # Build tool list
            tool_lines = []
            for ti, pos in enumerate(order):
                s = skills[pos]
                tool_lines.append(f"Tool {ti+1}: {s['name']}\n  Description: {s['description'][:200]}")
            tool_list = "\n\n".join(tool_lines)
            
            prompt = f"""{SYSTEM_PROMPT}

User Query: {query}

Available Tools:
{tool_list}

Select the best tool for this query. Respond with ONLY the tool name."""
            
            response = call_openclaw(prompt)
            
            if response is None:
                errors += 1
                time.sleep(BACKOFF_DELAY)
                continue
            
            # Parse response
            selected = None
            resp_lower = response.lower().strip()
            # Clean thinking tokens from Gemini
            for marker in ["[thought:", "[thinking"]:
                if marker in resp_lower:
                    resp_lower = resp_lower.split(marker)[0].strip()
                    # Also try after the marker
                    parts = response.lower().split("]")
                    resp_lower = parts[-1].strip() if parts else resp_lower
            
            for sname in skill_names:
                if sname.lower() in resp_lower:
                    selected = sname
                    break
            
            if selected is None:
                # Fallback: check for tool number
                for ti, pos in enumerate(order):
                    if f"tool {ti+1}" in resp_lower:
                        selected = skills[pos]["name"]
                        break
            
            if selected is None:
                errors += 1
                continue
            
            results.append({
                "query": query, "query_idx": q_idx,
                "rotation_idx": rot_idx,
                "skill_order": [skills[i]["name"] for i in order],
                "selected": selected,
                "raw_response": response[:300],
            })
            
            # Progress
            if call_idx % 5 == 0 or call_idx == 1:
                print(f"  [{call_idx:3d}/{total}] q={q_idx} rot={rot_idx} → {selected}", flush=True)
            
            time.sleep(BASE_DELAY)
    
    # Compute metrics
    sel_counts = Counter(r["selected"] for r in results)
    total_sel = sum(sel_counts.values())
    sel_dist = {name: sel_counts.get(name, 0) / max(total_sel, 1) for name in skill_names}
    
    # δ_API: TV distance from uniform
    uniform = 1.0 / K
    delta_api = 0.5 * sum(abs(sel_dist.get(n, 0) - uniform) for n in skill_names)
    
    # δ_pos: TV distance of position-based distribution from uniform
    pos_counts = Counter()
    for r in results:
        order = r["skill_order"]
        sel = r["selected"]
        if sel in order:
            pos_counts[order.index(sel)] += 1
    pos_total = sum(pos_counts.values())
    pos_dist = {i: pos_counts.get(i, 0) / max(pos_total, 1) for i in range(K)}
    delta_pos = 0.5 * sum(abs(pos_dist.get(i, 0) - uniform) for i in range(K))
    
    print(f"\n  ✅ {len(results)} successful, {errors} errors", flush=True)
    print(f"  δ_API = {delta_api:.4f}", flush=True)
    print(f"  δ_pos = {delta_pos:.4f}", flush=True)
    print(f"  Selection: {dict(sel_counts)}", flush=True)
    
    return {
        "cluster": name, "model": MODEL_NAME, "run_id": RUN_ID,
        "n_results": len(results), "n_errors": errors,
        "delta_api": delta_api, "delta_pos": delta_pos,
        "selection_dist": sel_dist, "position_dist": pos_dist,
        "results": results,
    }


def main():
    clusters = json.loads(CLUSTERS_FILE.read_text())
    
    print(f"{'='*60}", flush=True)
    print(f"EXP-1: Skill Selection Bias Benchmark", flush=True)
    print(f"Model: {MODEL_NAME} | Run: {RUN_ID} | Clusters: {len(clusters)}", flush=True)
    print(f"{'='*60}", flush=True)
    
    # Check for partial results to resume
    all_results = {}
    summary_file = RESULTS_DIR / f"exp1_{MODEL_NAME}_run{RUN_ID}_summary.json"
    
    for cluster_name, cluster_data in clusters.items():
        # Check if already completed
        cluster_file = RESULTS_DIR / f"exp1_{MODEL_NAME}_run{RUN_ID}_{cluster_name}.json"
        if cluster_file.exists():
            print(f"\n⏩ Skipping {cluster_name} (already completed)", flush=True)
            existing = json.loads(cluster_file.read_text())
            all_results[cluster_name] = existing
            continue
        
        skills = cluster_data["skills"][:5]
        query_file = QUERIES_DIR / f"{cluster_name}.json"
        if not query_file.exists():
            print(f"\n⚠️ No queries for {cluster_name}, skipping", flush=True)
            continue
        
        queries = json.loads(query_file.read_text())
        result = run_cluster(cluster_name, skills, queries)
        all_results[cluster_name] = result
        
        # Save immediately per cluster
        cluster_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"  💾 Saved → {cluster_file.name}", flush=True)
        
        # Update summary
        summary = {
            "model": MODEL_NAME, "run_id": RUN_ID,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "completed_clusters": list(all_results.keys()),
            "clusters": {k: {
                "delta_api": v["delta_api"], "delta_pos": v["delta_pos"],
                "selection_dist": v.get("selection_dist", {}),
                "n_results": v["n_results"], "n_errors": v["n_errors"],
            } for k, v in all_results.items()},
        }
        if len(all_results) > 1:
            apis = [v["delta_api"] for v in all_results.values()]
            poss = [v["delta_pos"] for v in all_results.values()]
            summary["delta_model"] = sum(a + p for a, p in zip(apis, poss)) / (2 * len(apis))
        summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        
        # Brief pause between clusters
        print(f"\n  ⏳ Cooling down 10s before next cluster...", flush=True)
        time.sleep(10)
    
    # Final summary
    if all_results:
        apis = [v["delta_api"] for v in all_results.values()]
        poss = [v["delta_pos"] for v in all_results.values()]
        delta_model = sum(a + p for a, p in zip(apis, poss)) / (2 * len(apis))
        
        print(f"\n{'='*60}", flush=True)
        print(f"FINAL RESULTS: δ_model = {delta_model:.4f}", flush=True)
        print(f"{'='*60}", flush=True)
        for name, r in all_results.items():
            print(f"  {name:25s} δ_API={r['delta_api']:.3f} δ_pos={r['delta_pos']:.3f} "
                  f"n={r['n_results']}", flush=True)


if __name__ == "__main__":
    main()
