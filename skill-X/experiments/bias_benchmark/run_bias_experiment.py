#!/usr/bin/env python3
"""
run_bias_experiment.py — Multi-model bias benchmark runner
===========================================================
Implements EXP-1 from the redesigned experiment plan.

For each cluster:
  1. Load 5 skills with their descriptions
  2. For each of 100 queries:
     a. Generate K=5 cyclic rotations of skill ordering
     b. For each rotation, ask the LLM to select the best skill
     c. Record the selection
  3. Compute δ_API, δ_pos, δ_model

Backends:
  --backend openclaw   Use `openclaw infer model run` (no API key needed!)
  --backend api        Use direct API calls (needs API keys)

Models (openclaw backend):
  gpt-4.1-mini         via azure-openai-responses
  gpt-4.1-nano         via azure-openai-responses
  gemini-2.5-pro       via github-copilot
  gemini-3-flash       via github-copilot (gemini-3-flash-preview)
  claude-haiku-4.5     via amazon-bedrock

Models (api backend):
  gpt-4.1-mini, claude-3.5-sonnet, gemini-2.5-flash, qwen3-8b

Usage:
    # Via OpenClaw (recommended — no API key needed):
    python run_bias_experiment.py --cluster web_search --model gpt-4.1-mini --run 1
    python run_bias_experiment.py --all --model gemini-3-flash --run 1

    # Via direct API:
    python run_bias_experiment.py --all --model gpt-4.1-mini --run 1 --backend api
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from bias_metrics import (
    analyze_cluster_results,
    compute_delta_model,
    cross_model_correlation,
    cyclic_rotations,
)

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CLUSTERS_FILE = BASE_DIR / "clusters_final.json"        # LLM-refined clusters
QUERIES_DIR = BASE_DIR / "queries"                       # 100 queries per cluster
RESULTS_DIR = BASE_DIR / "results"

# ──────────────────────────────────────────────────────────────────────────────
# System prompt for tool selection (following BiasBusters prompt design)
# ──────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an AI assistant that helps users select the most appropriate tool for their task.
Given a user query and a list of available tools with their descriptions, select exactly ONE tool that best matches the user's needs.

Respond with ONLY the tool name, nothing else. Do not explain your reasoning."""

def format_tool_list(skills: List[Dict], order: List[int]) -> str:
    """Format skills in the specified order for the LLM prompt.
    
    Args:
        skills: List of skill dicts with 'name' and 'description'
        order: List of indices specifying presentation order
    
    Returns:
        Formatted string listing tools
    """
    lines = []
    for idx, pos in enumerate(order):
        s = skills[pos]
        lines.append(f"Tool {idx+1}: {s['name']}")
        lines.append(f"  Description: {s['description'][:300]}")
        lines.append("")
    return "\n".join(lines)


def build_user_prompt(query: str, tool_list_str: str) -> str:
    """Build the user prompt for tool selection."""
    return f"""User Query: {query}

Available Tools:
{tool_list_str}

Select the best tool for this query. Respond with ONLY the tool name."""


# ──────────────────────────────────────────────────────────────────────────────
# OpenClaw CLI backend (no API key needed!)
# ──────────────────────────────────────────────────────────────────────────────
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "/opt/homebrew/bin/openclaw")
OPENCLAW_ENV = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}

# Map friendly model names to openclaw model IDs
OPENCLAW_MODEL_MAP = {
    "gpt-4.1-mini":      "gpt-4.1-mini",        # azure-openai-responses
    "gpt-4.1-nano":      "gpt-4.1-nano",        # azure-openai-responses
    "gpt-4o":            "gpt-4o",              # azure-openai-responses
    "gemini-2.5-pro":    "gemini-2.5-pro",      # github-copilot
    "gemini-3-flash":    "gemini-3-flash-preview",  # github-copilot
    "gemini-3-pro":      "gemini-3-pro-preview",    # github-copilot
    "claude-haiku-4.5":  "anthropic.claude-haiku-4-5-20251001-v1:0",  # bedrock
    "claude-sonnet-4":   "anthropic.claude-sonnet-4-20250514-v1:0",   # bedrock
}


def _call_openclaw(model: str, system: str, user: str, 
                   temperature: float = 0.0, retries: int = 3) -> Optional[str]:
    """Call model via `openclaw infer model run --gateway`.
    
    No API key needed — uses OpenClaw's built-in auth.
    Uses --gateway to route through the running gateway process (faster).
    IMPORTANT: Must use stdin=DEVNULL to prevent OpenClaw from hanging.
    """
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
                preexec_fn=os.setsid,
            )
            stdout, stderr = p.communicate(timeout=90)
            stdout_str = stdout.decode("utf-8", errors="replace")
            
            if p.returncode == 0 and stdout_str:
                # OpenClaw outputs pretty-printed JSON — parse entire stdout
                try:
                    d = json.loads(stdout_str.strip())
                    text = d.get("outputs", [{}])[0].get("text", "").strip()
                    if text:
                        return text
                except json.JSONDecodeError:
                    # Fallback: find first JSON object in output
                    import re
                    m = re.search(r'\{.*\}', stdout_str, re.DOTALL)
                    if m:
                        d = json.loads(m.group())
                        text = d.get("outputs", [{}])[0].get("text", "").strip()
                        if text:
                            return text
        except subprocess.TimeoutExpired:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            p.wait()
            if attempt < retries - 1:
                time.sleep(2)
        except (json.JSONDecodeError, Exception) as e:
            print(f"  [ERROR] openclaw call failed: {e}", flush=True)
            if attempt < retries - 1:
                time.sleep(2)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Direct API wrappers (needs API keys)
# ──────────────────────────────────────────────────────────────────────────────

# Current backend — set via --backend flag
_BACKEND = "api"  # "openclaw" or "api"

def call_model(model: str, system: str, user: str, 
               temperature: float = 0.0) -> Optional[str]:
    """Call the specified model and return the response text.
    
    Uses either OpenClaw CLI or direct API based on _BACKEND setting.
    """
    try:
        if _BACKEND == "openclaw":
            return _call_openclaw(model, system, user, temperature)
        
        # Direct API backend
        if model.startswith("gpt"):
            return _call_openai(model, system, user, temperature)
        elif model.startswith("claude"):
            return _call_anthropic(model, system, user, temperature)
        elif model.startswith("gemini"):
            return _call_gemini(model, system, user, temperature)
        elif model.startswith("qwen"):
            return _call_openrouter(model, system, user, temperature)
        else:
            raise ValueError(f"Unknown model: {model}")
    except Exception as e:
        print(f"  [ERROR] API call failed: {e}")
        return None


def _call_openai(model: str, system: str, user: str, temp: float) -> str:
    from openai import OpenAI
    client_kwargs = {"api_key": os.environ.get("OPENAI_API_KEY")}
    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temp,
        max_tokens=50,
    )
    return resp.choices[0].message.content.strip()


def _call_anthropic(model: str, system: str, user: str, temp: float) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=50,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=temp,
    )
    return resp.content[0].text.strip()


def _call_gemini(model: str, system: str, user: str, temp: float) -> str:
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ.get("GEMINI_API_KEY"),
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    resp = client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temp,
        max_tokens=50,
    )
    return resp.choices[0].message.content.strip()


def _call_openrouter(model: str, system: str, user: str, temp: float) -> str:
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ.get("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1"
    )
    model_map = {
        "qwen3-8b": "qwen/qwen3-8b",
    }
    resp = client.chat.completions.create(
        model=model_map.get(model, model),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temp,
        max_tokens=50,
    )
    return resp.choices[0].message.content.strip()


# ──────────────────────────────────────────────────────────────────────────────
# Core experiment loop
# ──────────────────────────────────────────────────────────────────────────────
def run_cluster_experiment(
    cluster_name: str,
    skills: List[Dict],
    queries: List[str],
    model: str,
    run_id: int = 1,
    delay: float = 0.1,
) -> Dict:
    """Run bias experiment for a single cluster.
    
    Args:
        cluster_name: Name of the cluster
        skills: List of 5 skill dicts with 'name' and 'description'
        queries: List of 100 queries
        model: Model identifier
        run_id: Run number for reproducibility
        delay: Seconds between API calls
    
    Returns:
        Dict with results and metrics
    """
    K = len(skills)
    skill_names = [s["name"] for s in skills]
    
    results = []
    errors = 0
    
    total_calls = len(queries) * K
    call_idx = 0
    
    print(f"\n{'='*60}")
    print(f"Cluster: {cluster_name} | Model: {model} | Run: {run_id}")
    print(f"Skills: {skill_names}")
    print(f"Queries: {len(queries)} | Rotations: {K} | Total calls: {total_calls}")
    print(f"{'='*60}")
    
    for q_idx, query in enumerate(queries):
        # Generate cyclic rotations
        indices = list(range(K))
        rotations = cyclic_rotations(indices, K)
        
        for rot_idx, order in enumerate(rotations):
            call_idx += 1
            
            # Build prompt
            tool_list = format_tool_list(skills, order)
            user_prompt = build_user_prompt(query, tool_list)
            
            # Call model
            response = call_model(model, SYSTEM_PROMPT, user_prompt)
            
            if response is None:
                errors += 1
                continue
            
            # Parse response — find which skill name matches
            selected = None
            response_lower = response.lower().strip()
            for name in skill_names:
                if name.lower() in response_lower:
                    selected = name
                    break
            
            if selected is None:
                # Fallback: check if response matches any tool number
                for tool_num, pos in enumerate(order):
                    if f"tool {tool_num+1}" in response_lower:
                        selected = skills[pos]["name"]
                        break
            
            if selected is None:
                errors += 1
                continue
            
            # Record
            ordered_names = [skills[i]["name"] for i in order]
            results.append({
                "query": query,
                "query_idx": q_idx,
                "rotation_idx": rot_idx,
                "skill_order": ordered_names,
                "selected": selected,
                "raw_response": response,
            })
            
            # Progress — print every 5 calls for real-time monitoring
            if call_idx % 5 == 0 or call_idx == 1:
                print(f"  [{call_idx}/{total_calls}] q={q_idx} rot={rot_idx} → {selected}", flush=True)
            
            time.sleep(delay)
    
    # Compute metrics
    metrics = analyze_cluster_results(results)
    
    print(f"\n  Results: {len(results)} successful, {errors} errors")
    print(f"  δ_API = {metrics['delta_api']:.4f}")
    print(f"  δ_pos = {metrics['delta_pos']:.4f}")
    print(f"  δ_cluster = {metrics['delta_cluster']:.4f}")
    print(f"  Selection distribution: {metrics.get('selection_dist', {})}")
    
    return {
        "cluster": cluster_name,
        "model": model,
        "run_id": run_id,
        "n_queries": len(queries),
        "n_skills": K,
        "n_results": len(results),
        "n_errors": errors,
        "metrics": metrics,
        "results": results,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main():
    global _BACKEND
    
    parser = argparse.ArgumentParser(description="Run bias benchmark experiment")
    parser.add_argument("--cluster", type=str, help="Cluster name (or --all)")
    parser.add_argument("--all", action="store_true", help="Run all clusters")
    parser.add_argument("--model", type=str, default="gpt-4.1-mini",
                       help="Model name (see --list-models for available options)")
    parser.add_argument("--backend", type=str, default="openclaw",
                       choices=["openclaw", "api"],
                       help="Backend: 'openclaw' (no API key needed) or 'api' (direct API)")
    parser.add_argument("--run", type=int, default=1, help="Run ID (1-3)")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between API calls (seconds)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without calling APIs")
    parser.add_argument("--list-models", action="store_true", help="List available models")
    args = parser.parse_args()
    
    if args.list_models:
        print("OpenClaw backend models (no API key needed):")
        for name, mid in OPENCLAW_MODEL_MAP.items():
            print(f"  {name:25s} → {mid}")
        print("\nDirect API backend models (needs API keys):")
        print("  gpt-4.1-mini, claude-3.5-sonnet, gemini-2.5-flash, qwen3-8b")
        return
    
    _BACKEND = args.backend
    print(f"Backend: {_BACKEND}")
    if _BACKEND == "openclaw":
        print(f"  Using: {OPENCLAW_BIN}")
        print(f"  Model map: {args.model} → {OPENCLAW_MODEL_MAP.get(args.model, args.model)}")
    
    # Load clusters
    if not CLUSTERS_FILE.exists():
        print(f"ERROR: {CLUSTERS_FILE} not found.")
        print("Run: python build_clusters.py && python refine_clusters.py first.")
        sys.exit(1)
    
    clusters = json.loads(CLUSTERS_FILE.read_text())
    
    # Determine which clusters to run
    if args.all:
        target_clusters = list(clusters.keys())
    elif args.cluster:
        if args.cluster not in clusters:
            print(f"ERROR: Unknown cluster '{args.cluster}'")
            print(f"Available: {list(clusters.keys())}")
            sys.exit(1)
        target_clusters = [args.cluster]
    else:
        print("ERROR: Specify --cluster <name> or --all")
        sys.exit(1)
    
    # Ensure output dir
    RESULTS_DIR.mkdir(exist_ok=True, parents=True)
    
    all_results = {}
    delta_apis = []
    delta_poss = []
    
    for cluster_name in target_clusters:
        cluster = clusters[cluster_name]
        skills = cluster["skills"][:5]  # Take exactly 5
        
        # Load queries
        query_file = QUERIES_DIR / f"{cluster_name}.json"
        if not query_file.exists():
            print(f"WARNING: No queries for {cluster_name}, skipping.")
            continue
        
        queries = json.loads(query_file.read_text())
        
        if args.dry_run:
            print(f"\n[DRY RUN] {cluster_name}:")
            print(f"  Skills: {[s['name'] for s in skills]}")
            print(f"  Queries: {len(queries)}")
            print(f"  Total calls: {len(queries) * len(skills)}")
            continue
        
        result = run_cluster_experiment(
            cluster_name=cluster_name,
            skills=skills,
            queries=queries,
            model=args.model,
            run_id=args.run,
            delay=args.delay,
        )
        
        all_results[cluster_name] = result
        delta_apis.append(result["metrics"]["delta_api"])
        delta_poss.append(result["metrics"]["delta_pos"])
    
    if args.dry_run:
        return
    
    # Compute model-level metric
    if delta_apis:
        delta_model = compute_delta_model(delta_apis, delta_poss)
        print(f"\n{'='*60}")
        print(f"Model-level bias: δ_model = {delta_model:.4f}")
        print(f"{'='*60}")
    
    # Save results
    out_file = RESULTS_DIR / f"exp1_{args.model}_run{args.run}.json"
    save_data = {
        "model": args.model,
        "run_id": args.run,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "delta_model": delta_model if delta_apis else None,
        "clusters": {k: {
            "delta_api": v["metrics"]["delta_api"],
            "delta_pos": v["metrics"]["delta_pos"],
            "delta_cluster": v["metrics"]["delta_cluster"],
            "selection_dist": v["metrics"]["selection_dist"],
            "position_dist": v["metrics"]["position_dist"],
            "n_results": v["n_results"],
            "n_errors": v["n_errors"],
        } for k, v in all_results.items()},
    }
    out_file.write_text(json.dumps(save_data, indent=2, ensure_ascii=False))
    print(f"\n✅ Saved results → {out_file}")
    
    # Also save full results (with individual responses) for analysis
    full_file = RESULTS_DIR / f"exp1_{args.model}_run{args.run}_full.json"
    full_file.write_text(json.dumps(all_results, indent=2, ensure_ascii=False, default=str))
    print(f"✅ Saved full results → {full_file}")


if __name__ == "__main__":
    main()
