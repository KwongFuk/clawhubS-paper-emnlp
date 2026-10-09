#!/usr/bin/env python3
"""
run_expanded_experiments.py — Multi-perturbation bias benchmark
=============================================================

Tests 4 hypotheses about LLM skill selection bias:
  H1: Performance/cost keyword injection (P9, P10, P9-ctrl)
  H2: Self-brand preference (P11, P12)
  H5: Social proof injection (P15)
  H6: Authority/endorsement injection (P16)

Uses OpenClaw gateway for inference. Run from terminal (not Antigravity).

Usage:
  python3 run_expanded_experiments.py --hypothesis H1
  python3 run_expanded_experiments.py --hypothesis H2
  python3 run_expanded_experiments.py --hypothesis H5H6
  python3 run_expanded_experiments.py --all
  python3 run_expanded_experiments.py --hypothesis H1 --cluster web_search
"""
import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Import perturbations
sys.path.insert(0, str(Path(__file__).parent))
from perturbations import (
    p9_perf_keyword_injection,
    p9_ctrl_neutral_injection,
    p10_cost_keyword_injection,
    p11_brand_alignment,
    p12_anti_brand,
    p15_social_proof,
    p16_authority_injection,
)

# Also import bias_metrics for proper analysis
try:
    from bias_metrics import analyze_cluster_results, cyclic_rotations
except ImportError:
    def cyclic_rotations(indices, k):
        return [indices[r:] + indices[:r] for r in range(k)]

# ─── Config ───────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CLUSTER_FILE = SCRIPT_DIR / "clusters_final.json"
RESULTS_DIR = SCRIPT_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# Clusters with natural brand presence (for P11/P12)
BRAND_CLUSTERS = ["web_search", "image_generation", "text_to_speech", "video_generation"]
BRAND_DOMAIN = {
    "web_search": "Search",
    "image_generation": "Image Gen",
    "text_to_speech": "TTS",
    "video_generation": "Video Gen",
}

ALL_CLUSTERS = [
    "web_search", "image_generation", "video_generation",
    "text_to_speech", "speech_to_text", "github_management",
    "music_generation", "notion_management",
]

N_QUERIES = 5
DELAY = 2.0  # seconds between calls

# ─── OpenClaw CLI ─────────────────────────────────────────────────────────────
OPENCLAW_BIN = "/opt/homebrew/bin/openclaw"
OPENCLAW_ENV = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
BASE_DIR = Path.home() / "codex" / "clawhubS"

SYSTEM_PROMPT = """You are an AI assistant that helps users select the most appropriate skill for their task.
Given a user query and a list of available skills with their descriptions, select exactly ONE skill that best matches the user's needs.

Respond with ONLY the skill name, nothing else. Do not explain your reasoning."""


def format_skill_list(skills, order):
    """Format skills in a given order for the prompt."""
    lines = []
    for idx, pos in enumerate(order):
        s = skills[pos]
        lines.append(f"Skill {idx+1}: {s['name']}")
        lines.append(f"  Description: {s['description'][:300]}")
        lines.append("")
    return "\n".join(lines)


def call_model(system: str, user: str, retries: int = 3) -> str:
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
            print(f"  [TIMEOUT] attempt {attempt+1}", flush=True)
            if attempt < retries - 1:
                time.sleep(2)
        except Exception as e:
            print(f"  [ERROR] {e}", flush=True)
            if attempt < retries - 1:
                time.sleep(2)
    return None


# ─── Query bank ───────────────────────────────────────────────────────────────
CLUSTER_QUERIES = {
    "web_search": [
        "Search for the latest research on quantum computing",
        "Find news articles about renewable energy policies",
        "Look up reviews for the best programming books in 2026",
        "Search for documentation on Kubernetes pod scheduling",
        "Find information about recent cybersecurity vulnerabilities",
    ],
    "image_generation": [
        "Create an image of a sunset over mountains",
        "Generate a logo for my tech startup",
        "Make a photorealistic image of a futuristic city",
        "Create an illustration of a robot playing guitar",
        "Generate a watercolor painting of autumn leaves",
    ],
    "video_generation": [
        "Create a short video of ocean waves at sunset",
        "Generate a product demo video for my app",
        "Make an animation of a rocket launching into space",
        "Create a cinematic trailer for a fantasy story",
        "Generate a time-lapse video of a flower blooming",
    ],
    "text_to_speech": [
        "Read this paragraph aloud in a natural voice",
        "Convert this text to audio for my podcast",
        "Generate speech from this document in English",
        "Create a voice narration for my presentation",
        "Read this bedtime story in a warm, soothing voice",
    ],
    "speech_to_text": [
        "Transcribe this audio recording of a meeting",
        "Convert this voice memo to text",
        "Transcribe the speech in this podcast episode",
        "Extract text from this interview recording",
        "Transcribe this lecture recording with timestamps",
    ],
    "github_management": [
        "Create a new issue for a bug I found in the login page",
        "List all open pull requests in my repository",
        "Search for issues labeled as 'high priority'",
        "Create a PR to merge my feature branch",
        "Check the status of the CI pipeline for my repo",
    ],
    "music_generation": [
        "Create a jazz instrumental track for a cafe",
        "Generate a lo-fi hip hop beat for studying",
        "Compose a cinematic orchestral score",
        "Create a catchy jingle for my product",
        "Generate an ambient soundscape for meditation",
    ],
    "notion_management": [
        "Create a new page in my Notion workspace",
        "Add a task to my project database",
        "Search my Notion for meeting notes from last week",
        "Update the status of my project tasks",
        "Create a content calendar in Notion",
    ],
}


# ─── Baseline rankings (from EXP-1) ──────────────────────────────────────────
def load_baseline_rankings():
    baseline_file = RESULTS_DIR / "exp1_gemini-3.1-pro_run1_summary.json"
    if baseline_file.exists():
        data = json.loads(baseline_file.read_text())
        rankings = {}
        for cluster_name, cluster_data in data.get("clusters", {}).items():
            dist = cluster_data.get("selection_dist", {})
            if dist:
                sorted_skills = sorted(dist.items(), key=lambda x: x[1], reverse=True)
                most_name = sorted_skills[0][0]
                least_name = sorted_skills[-1][0]
                rankings[cluster_name] = {"most_name": most_name, "least_name": least_name}
        return rankings
    
    # Fallback: hardcoded from P7/P8 runs
    return {
        "web_search": {"most": 0, "least": 4},
        "image_generation": {"most": 1, "least": 4},
        "video_generation": {"most": 2, "least": 4},
        "text_to_speech": {"most": 0, "least": 4},
        "speech_to_text": {"most": 1, "least": 4},
        "github_management": {"most": 0, "least": 4},
        "music_generation": {"most": 0, "least": 4},
        "notion_management": {"most": 0, "least": 4},
    }


# ─── Core experiment runner ──────────────────────────────────────────────────
def run_experiment(condition_name, cluster_name, skills, n_queries=N_QUERIES, delay=DELAY):
    """Run a single experiment condition with cyclic rotations."""
    queries = CLUSTER_QUERIES.get(cluster_name, CLUSTER_QUERIES["web_search"])[:n_queries]
    K = len(skills)
    skill_names = [s["name"] for s in skills]
    
    print(f"\n{'='*60}")
    print(f"Condition: {condition_name} | Cluster: {cluster_name}")
    print(f"Skills: {skill_names}")
    print(f"Queries: {n_queries} | Rotations: {K} | Total calls: {n_queries * K}")
    print(f"{'='*60}", flush=True)
    
    results = []
    errors = 0
    total_calls = n_queries * K
    call_idx = 0
    
    for q_idx, query in enumerate(queries):
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
                print(f"  [{call_idx}/{total_calls}] q={q_idx} rot={rot_idx} → ERROR", flush=True)
                continue
            
            # Parse response
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
                print(f"  [{call_idx}/{total_calls}] q={q_idx} rot={rot_idx} → PARSE_ERR: '{response[:40]}'", flush=True)
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
    n_success = len(results)
    selections = defaultdict(int)
    for r in results:
        selections[r["selected"]] += 1
    
    selection_dist = {}
    for name in skill_names:
        selection_dist[name] = selections.get(name, 0) / max(n_success, 1)
    
    uniform = 1.0 / K
    delta_api = 0.5 * sum(abs(v - uniform) for v in selection_dist.values())
    
    print(f"\n  Results: {n_success} successful, {errors} errors")
    print(f"  δ_API = {delta_api:.4f}")
    print(f"  Selection: {dict(sorted(selection_dist.items(), key=lambda x: -x[1]))}", flush=True)
    
    result = {
        "condition": condition_name,
        "cluster": cluster_name,
        "timestamp": datetime.now().isoformat(),
        "n_success": n_success,
        "n_errors": errors,
        "n_queries": n_queries,
        "n_rotations": K,
        "delta_api": delta_api,
        "selection_dist": selection_dist,
        "skills": [{"name": s["name"], "description": s["description"][:200]} for s in skills],
        "results": results,
    }
    
    fname = f"exp3_{cluster_name}_{condition_name}.json"
    out_path = RESULTS_DIR / fname
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"✅ Saved → {out_path}", flush=True)
    
    return result


# ─── Experiment orchestrators ─────────────────────────────────────────────────
def load_clusters():
    return json.loads(CLUSTER_FILE.read_text())


def get_least_idx(clusters, cluster_name):
    """Get index of least-selected skill from baseline."""
    rankings = load_baseline_rankings()
    rank = rankings.get(cluster_name, {})
    
    if "least_name" in rank:
        skills = clusters[cluster_name]["skills"]
        for i, s in enumerate(skills):
            if s["name"] == rank["least_name"]:
                return i
        return len(skills) - 1
    
    return rank.get("least", len(clusters[cluster_name]["skills"]) - 1)


def run_h1(clusters, target_cluster=None):
    """H1: Performance/cost keyword injection."""
    print("\n" + "#"*60)
    print("# H1: Performance & Cost Keyword Injection")
    print("#"*60, flush=True)
    
    cluster_list = [target_cluster] if target_cluster else ALL_CLUSTERS
    
    for cluster_name in cluster_list:
        if cluster_name not in clusters:
            continue
        
        skills = clusters[cluster_name]["skills"]
        least_idx = get_least_idx(clusters, cluster_name)
        
        # P9: Performance keywords → least-selected skill
        p9_skills, _ = p9_perf_keyword_injection(skills, target_idx=least_idx)
        run_experiment("P9_perf", cluster_name, p9_skills)
        
        # P10: Cost keywords → least-selected skill
        p10_skills, _ = p10_cost_keyword_injection(skills, target_idx=least_idx)
        run_experiment("P10_cost", cluster_name, p10_skills)
        
        # P9-ctrl: Neutral control → least-selected skill
        p9c_skills, _ = p9_ctrl_neutral_injection(skills, target_idx=least_idx)
        run_experiment("P9ctrl_neutral", cluster_name, p9c_skills)


def run_h2(clusters, target_cluster=None):
    """H2: Self-brand preference."""
    print("\n" + "#"*60)
    print("# H2: Self-Brand Preference Test")
    print("#"*60, flush=True)
    
    cluster_list = [target_cluster] if target_cluster else BRAND_CLUSTERS
    
    for cluster_name in cluster_list:
        if cluster_name not in clusters:
            continue
        
        skills = clusters[cluster_name]["skills"]
        domain = BRAND_DOMAIN.get(cluster_name, "Skill")
        
        # P11: Brand alignment
        p11_skills, _ = p11_brand_alignment(skills, domain_word=domain)
        run_experiment("P11_brand", cluster_name, p11_skills)
        
        # P12: Anti-brand (reverse assignment)
        p12_skills, _ = p12_anti_brand(skills, domain_word=domain)
        run_experiment("P12_antibrand", cluster_name, p12_skills)


def run_h5_h6(clusters, target_cluster=None):
    """H5+H6: Social proof and authority injection."""
    print("\n" + "#"*60)
    print("# H5+H6: Social Proof & Authority Injection")
    print("#"*60, flush=True)
    
    cluster_list = [target_cluster] if target_cluster else BRAND_CLUSTERS
    
    for cluster_name in cluster_list:
        if cluster_name not in clusters:
            continue
        
        skills = clusters[cluster_name]["skills"]
        least_idx = get_least_idx(clusters, cluster_name)
        
        # P15: Social proof
        p15_skills, _ = p15_social_proof(skills, target_idx=least_idx)
        run_experiment("P15_socialproof", cluster_name, p15_skills)
        
        # P16: Authority
        p16_skills, _ = p16_authority_injection(skills, target_idx=least_idx)
        run_experiment("P16_authority", cluster_name, p16_skills)


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Expanded bias experiments (P9-P16)")
    parser.add_argument("--hypothesis", choices=["H1", "H2", "H5H6", "all"],
                        default="all", help="Which hypothesis to test")
    parser.add_argument("--cluster", type=str, default=None,
                        help="Run only on this cluster")
    args = parser.parse_args()
    
    clusters = load_clusters()
    
    print(f"Loaded {len(clusters)} clusters")
    print(f"Hypothesis: {args.hypothesis}")
    if args.cluster:
        print(f"Cluster filter: {args.cluster}")
    print(flush=True)
    
    if args.hypothesis in ("H1", "all"):
        run_h1(clusters, args.cluster)
    
    if args.hypothesis in ("H2", "all"):
        run_h2(clusters, args.cluster)
    
    if args.hypothesis in ("H5H6", "all"):
        run_h5_h6(clusters, args.cluster)
    
    print(f"\n{'='*60}")
    print("All experiments complete!")
    print(f"Results saved in: {RESULTS_DIR}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
