#!/usr/bin/env python3
"""
run_perturbation_exp.py — EXP-2: Causal Description Perturbation Benchmark
============================================================================
Reads EXP-1 baseline results, applies 7 perturbation types (P1-P7),
and re-runs the same queries to measure causal impact of each feature.

Design:
  - For each completed cluster from EXP-1:
    1. Load baseline selection distribution
    2. Identify most-selected and least-selected skills
    3. Apply each perturbation type
    4. Run 10 queries × 5 rotations = 50 calls per perturbation
    5. Compare δ_API between baseline and perturbed

  - 7 perturbations × N completed clusters × 50 calls each
  - Resume-safe: saves after each (cluster, perturbation) pair

Output:
  - results/exp2_{model}_run{id}_{cluster}_{perturbation}.json (per pair)
  - results/exp2_summary.json (aggregated)

Usage:
    PYTHONUNBUFFERED=1 python3 -u run_perturbation_exp.py
    PYTHONUNBUFFERED=1 python3 -u run_perturbation_exp.py --cluster web_search --perturb P7
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).parent
CLUSTERS_FILE = BASE_DIR / "clusters_final.json"
QUERIES_DIR = BASE_DIR / "queries"
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "/opt/homebrew/bin/openclaw")
OPENCLAW_ENV = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}

MODEL_NAME = "gemini-3.1-pro"
RUN_ID = 1
BASE_DELAY = 3.0
BACKOFF_DELAY = 15.0
MAX_RETRIES = 3
TIMEOUT = 90

SYSTEM_PROMPT = """You are an AI assistant that helps users select the most appropriate tool for their task.
Given a user query and a list of available tools with their descriptions, select exactly ONE tool that best matches the user's needs.
Respond with ONLY the tool name, nothing else. Do not explain your reasoning."""

# Import perturbation functions
from perturbations import (
    p1_full_name_scramble, p2_name_shuffle, p3_single_name_perturb,
    p4_desc_param_scramble, p5_desc_only_scramble, p6_targeted_desc_scramble,
    p7_desc_transfer,
)

PERTURBATION_FNS = {
    "P1": ("Full Name Scramble", p1_full_name_scramble),
    "P2": ("Name Shuffle", p2_name_shuffle),
    "P3": ("Single Name Perturb", p3_single_name_perturb),
    "P4": ("Desc+Param Scramble", p4_desc_param_scramble),
    "P5": ("Desc-Only Scramble", p5_desc_only_scramble),
    "P6": ("Targeted Desc Scramble", p6_targeted_desc_scramble),
    "P7": ("Desc Transfer (Best↔Worst)", p7_desc_transfer),
}


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
    base = list(range(n))
    return [base[i:] + base[:i] for i in range(n)]


def run_perturbed_cluster(cluster_name: str, skills: list, queries: list,
                          perturbation_id: str, label: str) -> dict:
    """Run one cluster with perturbed skills."""
    K = len(skills)
    skill_names = [s["name"] for s in skills]
    # Also track original names for name-scrambled perturbations
    original_names = [s.get("original_name", s["name"]) for s in skills]
    rotations = cyclic_rotations(K)
    total = len(queries) * K

    print(f"\n  [{perturbation_id}] {label}", flush=True)
    print(f"    Skills: {skill_names}", flush=True)

    results = []
    errors = 0
    call_idx = 0

    for q_idx, query in enumerate(queries):
        for rot_idx, order in enumerate(rotations):
            call_idx += 1

            tool_lines = []
            for ti, pos in enumerate(order):
                s = skills[pos]
                tool_lines.append(
                    f"Tool {ti+1}: {s['name']}\n  Description: {s['description'][:200]}")
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

            selected = None
            resp_lower = response.lower().strip()
            # Clean thinking tokens
            for marker in ["[thought:", "[thinking"]:
                if marker in resp_lower:
                    parts = response.lower().split("]")
                    resp_lower = parts[-1].strip() if parts else resp_lower

            for sname in skill_names:
                if sname.lower() in resp_lower:
                    selected = sname
                    break

            if selected is None:
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

            if call_idx % 5 == 0 or call_idx == 1:
                print(f"    [{call_idx:3d}/{total}] q={q_idx} rot={rot_idx} → {selected}",
                      flush=True)

            time.sleep(BASE_DELAY)

    # Compute metrics
    sel_counts = Counter(r["selected"] for r in results)
    total_sel = sum(sel_counts.values())
    sel_dist = {name: sel_counts.get(name, 0) / max(total_sel, 1) for name in skill_names}

    uniform = 1.0 / K
    delta_api = 0.5 * sum(abs(sel_dist.get(n, 0) - uniform) for n in skill_names)

    pos_counts = Counter()
    for r in results:
        order = r["skill_order"]
        sel = r["selected"]
        if sel in order:
            pos_counts[order.index(sel)] += 1
    pos_total = sum(pos_counts.values())
    pos_dist = {i: pos_counts.get(i, 0) / max(pos_total, 1) for i in range(K)}
    delta_pos = 0.5 * sum(abs(pos_dist.get(i, 0) - uniform) for i in range(K))

    print(f"    ✅ {len(results)} ok, {errors} err | δ_API={delta_api:.4f} δ_pos={delta_pos:.4f}",
          flush=True)
    print(f"    Selection: {dict(sel_counts)}", flush=True)

    return {
        "cluster": cluster_name, "perturbation": perturbation_id,
        "perturbation_label": label,
        "model": MODEL_NAME, "run_id": RUN_ID,
        "n_results": len(results), "n_errors": errors,
        "delta_api": delta_api, "delta_pos": delta_pos,
        "selection_dist": sel_dist, "position_dist": pos_dist,
        "original_names": original_names,
        "perturbed_names": skill_names,
        "results": results,
    }


def get_baseline_info(cluster_name: str) -> dict | None:
    """Load EXP-1 baseline results for a cluster."""
    f = RESULTS_DIR / f"exp1_{MODEL_NAME}_run{RUN_ID}_{cluster_name}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text())


def find_most_least(baseline: dict) -> tuple[int, int]:
    """Find indices of most and least selected skills from baseline."""
    sel_dist = baseline.get("selection_dist", {})
    clusters = json.loads(CLUSTERS_FILE.read_text())
    skills = clusters[baseline["cluster"]]["skills"][:5]
    
    rates = [(i, sel_dist.get(s["name"], 0)) for i, s in enumerate(skills)]
    rates.sort(key=lambda x: -x[1])
    
    most_idx = rates[0][0]
    least_idx = rates[-1][0]
    return most_idx, least_idx


def main():
    parser = argparse.ArgumentParser(description="EXP-2: Perturbation benchmark")
    parser.add_argument("--cluster", type=str, help="Specific cluster (default: all completed)")
    parser.add_argument("--perturb", type=str, help="Specific perturbation (P1-P7)")
    args = parser.parse_args()

    clusters = json.loads(CLUSTERS_FILE.read_text())

    # Find completed EXP-1 clusters
    completed = []
    for name in clusters:
        baseline = get_baseline_info(name)
        if baseline and baseline.get("n_results", 0) >= 40:
            completed.append(name)

    if args.cluster:
        if args.cluster not in completed:
            print(f"❌ {args.cluster} not in completed clusters: {completed}")
            sys.exit(1)
        completed = [args.cluster]

    perturbation_ids = list(PERTURBATION_FNS.keys())
    if args.perturb:
        if args.perturb not in PERTURBATION_FNS:
            print(f"❌ Unknown perturbation: {args.perturb}. Available: {perturbation_ids}")
            sys.exit(1)
        perturbation_ids = [args.perturb]

    total_pairs = len(completed) * len(perturbation_ids)
    print(f"{'='*70}", flush=True)
    print(f"EXP-2: Description Perturbation Benchmark", flush=True)
    print(f"Clusters: {completed} ({len(completed)})", flush=True)
    print(f"Perturbations: {perturbation_ids} ({len(perturbation_ids)})", flush=True)
    print(f"Total: {total_pairs} pairs × 50 calls = {total_pairs * 50} calls", flush=True)
    print(f"{'='*70}", flush=True)

    all_results = {}
    pair_idx = 0

    for cluster_name in completed:
        baseline = get_baseline_info(cluster_name)
        most_idx, least_idx = find_most_least(baseline)
        skills_orig = clusters[cluster_name]["skills"][:5]

        query_file = QUERIES_DIR / f"{cluster_name}.json"
        if not query_file.exists():
            print(f"⚠️ No queries for {cluster_name}", flush=True)
            continue
        queries = json.loads(query_file.read_text())

        baseline_delta = baseline["delta_api"]
        print(f"\n{'='*70}", flush=True)
        print(f"Cluster: {cluster_name} | Baseline δ_API={baseline_delta:.3f}", flush=True)
        print(f"  Most selected: {skills_orig[most_idx]['name']} "
              f"(rate={baseline['selection_dist'].get(skills_orig[most_idx]['name'], 0):.1%})", flush=True)
        print(f"  Least selected: {skills_orig[least_idx]['name']} "
              f"(rate={baseline['selection_dist'].get(skills_orig[least_idx]['name'], 0):.1%})", flush=True)

        for pid in perturbation_ids:
            pair_idx += 1
            label, fn = PERTURBATION_FNS[pid]

            # Check if already done
            out_file = RESULTS_DIR / f"exp2_{MODEL_NAME}_run{RUN_ID}_{cluster_name}_{pid}.json"
            if out_file.exists():
                print(f"\n  ⏩ [{pair_idx}/{total_pairs}] {cluster_name}/{pid} already done", flush=True)
                existing = json.loads(out_file.read_text())
                all_results[f"{cluster_name}_{pid}"] = existing
                continue

            print(f"\n  [{pair_idx}/{total_pairs}] {cluster_name} × {pid}", flush=True)

            # Apply perturbation
            import random
            random.seed(42)
            if pid in ("P3", "P6"):
                perturbed, _ = fn(skills_orig, most_idx)
            elif pid == "P7":
                perturbed, _ = fn(skills_orig, most_idx, least_idx)
            else:
                perturbed, _ = fn(skills_orig)

            result = run_perturbed_cluster(
                cluster_name, perturbed, queries, pid, label)

            # Add baseline comparison
            result["baseline_delta_api"] = baseline_delta
            result["delta_shift"] = result["delta_api"] - baseline_delta
            result["most_selected_idx"] = most_idx
            result["least_selected_idx"] = least_idx

            # Save immediately
            out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))
            print(f"    💾 Saved → {out_file.name}", flush=True)
            print(f"    Δ(δ_API) = {result['delta_shift']:+.4f} "
                  f"({baseline_delta:.3f} → {result['delta_api']:.3f})", flush=True)

            all_results[f"{cluster_name}_{pid}"] = result

            # Cool down
            time.sleep(5)

    # Final summary
    if all_results:
        print(f"\n{'='*70}", flush=True)
        print(f"EXP-2 SUMMARY", flush=True)
        print(f"{'='*70}", flush=True)
        print(f"{'Cluster/Perturb':<35s} {'Baseline':>9s} {'Perturbed':>10s} {'Shift':>8s}", flush=True)
        print("-" * 65, flush=True)

        for key, r in sorted(all_results.items()):
            bl = r.get("baseline_delta_api", 0)
            pt = r["delta_api"]
            shift = r.get("delta_shift", pt - bl)
            marker = " ★" if abs(shift) > 0.1 else ""
            print(f"  {key:<33s} {bl:>8.3f} {pt:>10.3f} {shift:>+8.3f}{marker}", flush=True)

        # Per-perturbation average shift
        print(f"\nPer-perturbation average shift:", flush=True)
        for pid in perturbation_ids:
            shifts = [r.get("delta_shift", 0) for k, r in all_results.items() if pid in k]
            if shifts:
                avg = sum(shifts) / len(shifts)
                print(f"  {pid}: avg Δ(δ_API) = {avg:+.4f} (n={len(shifts)})", flush=True)

        # Save summary
        summary = {
            "model": MODEL_NAME, "run_id": RUN_ID,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "n_pairs": len(all_results),
            "pairs": {k: {
                "delta_api": v["delta_api"], "delta_pos": v["delta_pos"],
                "baseline_delta_api": v.get("baseline_delta_api"),
                "delta_shift": v.get("delta_shift"),
                "selection_dist": v.get("selection_dist", {}),
            } for k, v in all_results.items()},
        }
        sf = RESULTS_DIR / "exp2_summary.json"
        sf.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        print(f"\n✅ Summary → {sf}", flush=True)


if __name__ == "__main__":
    main()
