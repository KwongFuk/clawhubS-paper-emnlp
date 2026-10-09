#!/usr/bin/env python3
"""
Run targeted perturbation experiments (P7 + P8) on key clusters.
P7 = Description Transfer: swap top-1 ↔ bottom-1 descriptions
P8 = Our Repair: use expert-repaired descriptions

These are the most causally informative perturbations:
- P7 tests whether selection follows description content (not name/brand)
- P8 tests whether our repair prescription actually works
"""
import json
import os
import sys
import time
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent / "bias_benchmark"
CLUSTERS_FILE = BASE_DIR / "clusters_final.json"
QUERIES_DIR = BASE_DIR / "queries"
RESULTS_DIR = BASE_DIR / "results"
REPAIRS_FILE = Path(os.path.expanduser("~/codex/clawhubS/paper-emnlp/analysis_output/description_repairs.json"))

sys.path.insert(0, str(BASE_DIR))
from bias_metrics import analyze_cluster_results, cyclic_rotations
from perturbations import p7_desc_transfer, p8_ic1_desc_repair

# Load data
clusters = json.loads(CLUSTERS_FILE.read_text())
repairs = json.loads(REPAIRS_FILE.read_text())

# Load existing baseline results
baseline_file = RESULTS_DIR / "exp1_gemini-3.1-pro_run1_summary.json"
baseline = json.loads(baseline_file.read_text())

# ============================================================
# OpenClaw inference (rate-limited, use wisely)
# ============================================================
OPENCLAW_BIN = "/opt/homebrew/bin/openclaw"
OPENCLAW_ENV = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}

SYSTEM_PROMPT = """You are an AI assistant that helps users select the most appropriate skill for their task.
Given a user query and a list of available skills with their descriptions, select exactly ONE skill that best matches the user's needs.

Respond with ONLY the skill name, nothing else. Do not explain your reasoning."""

def call_model(system: str, user: str, retries: int = 2) -> str:
    """Call model via openclaw gateway."""
    prompt = f"{system}\n\n{user}"
    for attempt in range(retries):
        try:
            cmd = [OPENCLAW_BIN, "infer", "model", "run",
                   "--gateway", "--prompt", prompt, "--json"]
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, env=OPENCLAW_ENV,
                cwd=str(BASE_DIR), preexec_fn=os.setsid)
            stdout, stderr = p.communicate(timeout=90)
            
            if p.returncode == 0:
                import re
                text = stdout.decode("utf-8", errors="replace")
                try:
                    d = json.loads(text.strip())
                    return d.get("outputs", [{}])[0].get("text", "").strip()
                except json.JSONDecodeError:
                    m = re.search(r'\{.*\}', text, re.DOTALL)
                    if m:
                        d = json.loads(m.group())
                        return d.get("outputs", [{}])[0].get("text", "").strip()
            
            stderr_text = stderr.decode("utf-8", errors="replace")
            if "exhausted" in stderr_text or "quota" in stderr_text:
                import re
                m = re.search(r'reset after (\d+)m', stderr_text)
                wait = int(m.group(1)) * 60 + 10 if m else 60
                print(f"  [QUOTA] Waiting {wait}s for quota reset...", flush=True)
                time.sleep(wait)
            elif attempt < retries - 1:
                time.sleep(3)
                
        except subprocess.TimeoutExpired:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            p.wait()
            if attempt < retries - 1:
                time.sleep(2)
        except Exception as e:
            print(f"  [ERROR] {e}", flush=True)
            if attempt < retries - 1:
                time.sleep(2)
    return None


def format_skill_list(skills, order):
    lines = []
    for idx, pos in enumerate(order):
        s = skills[pos]
        lines.append(f"Skill {idx+1}: {s['name']}")
        lines.append(f"  Description: {s['description'][:300]}")
        lines.append("")
    return "\n".join(lines)


def run_perturbation_experiment(cluster_name, skills, queries, perturbation_label,
                                 n_queries=5, delay=2.0):
    """Run a reduced perturbation experiment (5 queries × 5 rotations = 25 calls)."""
    K = len(skills)
    skill_names = [s["name"] for s in skills]
    results = []
    errors = 0
    
    queries_subset = queries[:n_queries]
    total_calls = n_queries * K
    call_idx = 0
    
    print(f"\n{'='*60}")
    print(f"Perturbation: {perturbation_label} | Cluster: {cluster_name}")
    print(f"Skills: {skill_names}")
    print(f"Queries: {n_queries} | Rotations: {K} | Total calls: {total_calls}")
    print(f"{'='*60}")
    
    for q_idx, query in enumerate(queries_subset):
        indices = list(range(K))
        rotations = cyclic_rotations(indices, K)
        
        for rot_idx, order in enumerate(rotations):
            call_idx += 1
            
            tool_list = format_skill_list(skills, order)
            user_prompt = f"""User Query: {query}

Available Skills:
{tool_list}

Select the best skill for this query. Respond with ONLY the skill name."""
            
            response = call_model(SYSTEM_PROMPT, user_prompt)
            
            if response is None:
                errors += 1
                print(f"  [{call_idx}/{total_calls}] ERROR", flush=True)
                continue
            
            # Parse
            selected = None
            response_lower = response.lower().strip()
            for name in skill_names:
                if name.lower() in response_lower:
                    selected = name
                    break
            
            if selected is None:
                for tool_num, pos in enumerate(order):
                    if f"skill {tool_num+1}" in response_lower or f"tool {tool_num+1}" in response_lower:
                        selected = skills[pos]["name"]
                        break
            
            if selected is None:
                errors += 1
                print(f"  [{call_idx}/{total_calls}] PARSE_ERROR: '{response[:50]}'", flush=True)
                continue
            
            ordered_names = [skills[i]["name"] for i in order]
            results.append({
                "query": query,
                "query_idx": q_idx,
                "rotation_idx": rot_idx,
                "skill_order": ordered_names,
                "selected": selected,
            })
            
            print(f"  [{call_idx}/{total_calls}] q={q_idx} rot={rot_idx} → {selected}", flush=True)
            time.sleep(delay)
    
    # Compute metrics
    metrics = analyze_cluster_results(results) if results else {"delta_api": 0, "delta_pos": 0, "selection_dist": {}}
    
    print(f"\n  Results: {len(results)} successful, {errors} errors")
    if results:
        print(f"  δ_API = {metrics['delta_api']:.4f}")
        print(f"  Selection: {metrics.get('selection_dist', {})}")
    
    return {
        "perturbation": perturbation_label,
        "cluster": cluster_name,
        "n_queries": n_queries,
        "n_results": len(results),
        "n_errors": errors,
        "metrics": metrics,
        "results": results,
    }


# ============================================================
# Main: run P7 + P8 on web_search and image_generation
# ============================================================
TARGET_CLUSTERS = ["web_search", "image_generation"]

for cluster_name in TARGET_CLUSTERS:
    cluster = clusters[cluster_name]
    skills = cluster["skills"][:5]
    
    # Load queries
    query_file = QUERIES_DIR / f"{cluster_name}.json"
    queries = json.loads(query_file.read_text())
    
    # Get baseline selection distribution
    sel_dist = baseline["clusters"][cluster_name]["selection_dist"]
    sorted_by_sel = sorted(sel_dist.items(), key=lambda x: -x[1])
    most_sel_name = sorted_by_sel[0][0]
    least_sel_name = sorted_by_sel[-1][0]
    
    most_idx = next(i for i, s in enumerate(skills) if s["name"] == most_sel_name)
    least_idx = next(i for i, s in enumerate(skills) if s["name"] == least_sel_name)
    
    print(f"\n{'#'*60}")
    print(f"# {cluster_name}: top={most_sel_name} (idx {most_idx}), bottom={least_sel_name} (idx {least_idx})")
    print(f"{'#'*60}")
    
    # ── P7: Description Transfer ──
    p7_skills, p7_label = p7_desc_transfer(skills, most_idx, least_idx)
    p7_result = run_perturbation_experiment(cluster_name, p7_skills, queries, p7_label, n_queries=5)
    
    out_file = RESULTS_DIR / f"exp2_gemini-3.1-pro_run1_{cluster_name}_P7_causal.json"
    out_file.write_text(json.dumps(p7_result, indent=2, ensure_ascii=False))
    print(f"✅ Saved → {out_file}")
    
    # ── P8: Our Repair ──
    repair_map = {}
    for sid, repair_data in repairs.items():
        if repair_data["cluster"] == cluster_name:
            repair_map[repair_data["name"]] = repair_data["repaired_desc"]
    
    if repair_map:
        p8_skills, p8_label = p8_ic1_desc_repair(skills, repair_map)
        p8_result = run_perturbation_experiment(cluster_name, p8_skills, queries, p8_label, n_queries=5)
        
        out_file = RESULTS_DIR / f"exp2_gemini-3.1-pro_run1_{cluster_name}_P8_repair.json"
        out_file.write_text(json.dumps(p8_result, indent=2, ensure_ascii=False))
        print(f"✅ Saved → {out_file}")
    else:
        print(f"  No repairs for {cluster_name}, skipping P8")

print(f"\n{'='*60}")
print("All perturbation experiments complete!")
print(f"{'='*60}")
