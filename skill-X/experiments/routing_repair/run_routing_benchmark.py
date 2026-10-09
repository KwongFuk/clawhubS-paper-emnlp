#!/usr/bin/env python3
"""
run_routing_benchmark.py — Phase 4: Large-Scale Routing Evaluation
===================================================================
For each skill × query × condition × router:
  1. Build a routing prompt with distractor skills
  2. Ask the router to select the best skill
  3. Record whether the correct skill was selected

Checkpoint-safe: saves results every 5 skills.
Resume-safe: skips already-completed skill×condition×router combos.

Total calls: 110 skills × 8 queries × 4 conditions × 3 routers = 10,560
"""
import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sqlite3
import sys
import time
from pathlib import Path

SEED = 42
random.seed(SEED)

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
CACHE_DIR = BASE_DIR / ".cache_route"

DB_PATH = Path("/Users/guangfu/codex/clawhubS/data/experiment1_split/merged.sqlite3")
OPENCLAW_BIN = "openclaw"

# Number of distractor skills per routing test
N_DISTRACTORS = 29  # 29 distractors + 1 target = 30 total

# Router configurations
ROUTERS = {
    "openclaw_default": {
        "name": "OpenClaw Default",
        "model": None,  # Uses default openclaw model
        "temperature": 0,
    },
    "gpt54_mini": {
        "name": "GPT-5.4-mini",
        "model": "gpt-5.4-mini",
        "temperature": 0,
    },
    "gemini_flash": {
        "name": "Gemini 2.5 Flash",
        "model": "gemini-2.5-flash",
        "temperature": 0,
    },
}

# Description conditions
CONDITIONS = ["original", "minimal_repair", "length_control", "structured_repair"]


# ── Distractor Pool ──────────────────────────────────────────────────────────

def build_distractor_pool(db_path: Path, n: int = 500) -> list[dict]:
    """Load top-N skills by downloads as distractor pool."""
    db = sqlite3.connect(str(db_path))
    rows = db.execute("""
        SELECT skill_id, name, description
        FROM skills
        WHERE description IS NOT NULL AND LENGTH(description) > 30
        ORDER BY downloads DESC
        LIMIT ?
    """, (n,)).fetchall()
    db.close()
    return [{"skill_id": r[0], "name": r[1], "description": r[2]} for r in rows]


def select_distractors(pool: list[dict], target_id: str,
                      n: int = N_DISTRACTORS) -> list[dict]:
    """Select N random distractors, excluding the target skill."""
    eligible = [s for s in pool if s["skill_id"] != target_id]
    return random.sample(eligible, min(n, len(eligible)))


# ── LLM Router ───────────────────────────────────────────────────────────────

ROUTING_PROMPT = """\
You are a skill router. Pick the ONE skill that best matches the user request.

User request: {query}

Skills:
{skill_list}

Reply with ONLY a single number (1-{n_skills}). If none match, reply 0."""


def _call_gemini_cli(prompt: str, retries: int = 2) -> str:
    """Call Gemini via `gemini` CLI (uses Google login auth, no API key)."""
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                ["gemini", "-p", prompt, "-m", "gemini-2.5-flash"],
                capture_output=True, text=True, timeout=60,
                stdin=subprocess.DEVNULL,
            )
            # Filter out warning lines
            lines = [l.strip() for l in result.stdout.strip().split("\n")
                     if l.strip() and not l.strip().startswith("Warning:") 
                     and not l.strip().startswith("Ripgrep")]
            text = lines[-1] if lines else ""
            if text:
                return text
        except subprocess.TimeoutExpired:
            if attempt < retries:
                time.sleep((attempt + 1) * 3)
        except Exception as e:
            if attempt < retries:
                time.sleep((attempt + 1) * 2)
            else:
                print(f"  [WARN] gemini CLI failed: {e}")
    return ""


def llm_call(prompt: str, cache_key: str = "", model: str = None,
             retries: int = 2) -> str:
    """Run LLM — uses direct API for gemini/gpt, openclaw CLI for default."""
    if cache_key:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cf = CACHE_DIR / f"{cache_key}.txt"
        if cf.exists():
            return cf.read_text().strip()

    # Use gemini CLI for gemini models (openclaw CLI blocks them)
    if model and "gemini" in model:
        text = _call_gemini_cli(prompt, retries)
        if cache_key and text:
            (CACHE_DIR / f"{cache_key}.txt").write_text(text)
        return text if text else "EMPTY"

    # Fallback: openclaw CLI (for openclaw_default)
    cmd = [OPENCLAW_BIN, "infer", "model", "run", "--prompt", prompt, "--json"]
    if model:
        cmd.extend(["--model", model])

    for attempt in range(retries + 1):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            output = result.stdout.strip()
            try:
                j = json.loads(output)
                text = j.get("outputs", [{}])[0].get("text", "").strip()
            except Exception:
                lines = [l.strip() for l in output.split("\n") if l.strip()]
                text = lines[-1] if lines else ""

            # Retry on empty response (don't cache empties)
            if not text and attempt < retries:
                wait = (attempt + 1) * 3
                time.sleep(wait)
                continue

            if cache_key and text:
                (CACHE_DIR / f"{cache_key}.txt").write_text(text)
            return text

        except subprocess.TimeoutExpired:
            wait = (attempt + 1) * 5
            print(f"  [WARN] Timeout (attempt {attempt+1}), retry in {wait}s")
            time.sleep(wait)

    if cache_key:
        (CACHE_DIR / f"{cache_key}.txt").write_text("EMPTY")
    return "EMPTY"


def route_query(query: str, target: dict, distractors: list[dict],
                condition: str, repairs: dict, router_key: str,
                target_pos: int = -1) -> dict:
    """Route a single query and return the result."""
    router = ROUTERS[router_key]

    # Build skill list with target inserted at deterministic position
    target_desc = repairs.get(condition, target["description"])
    target_entry = {"name": target["name"], "description": target_desc}

    entries = [{"name": d["name"], "description": d["description"][:150]}
               for d in distractors]

    # Use deterministic target position (based on skill_id hash)
    if target_pos < 0:
        target_pos = int(hashlib.md5(target["skill_id"].encode()).hexdigest(), 16) % (len(entries) + 1)
    entries.insert(target_pos, target_entry)

    # Build numbered list (description only, no names — clean routing signal)
    skill_list = "\n".join(
        f"{i+1}. {e['description'][:150]}"
        for i, e in enumerate(entries)
    )

    prompt = ROUTING_PROMPT.format(
        query=query, skill_list=skill_list, n_skills=len(entries)
    )

    # Cache key includes all variable factors
    ck_input = f"{query}|{condition}|{router_key}|{target['skill_id']}|{target_pos}"
    ck = hashlib.sha256(ck_input.encode()).hexdigest()[:24]

    raw = llm_call(prompt, cache_key=f"route_{ck}", model=router.get("model"))

    # Parse response
    m = re.search(r'\b(\d+)\b', raw.strip())
    chosen_idx = int(m.group(1)) - 1 if m else -1

    # Check if correct
    correct = (chosen_idx == target_pos)
    chosen_name = entries[chosen_idx]["name"] if 0 <= chosen_idx < len(entries) else "NONE"

    return {
        "chosen_idx": chosen_idx,
        "chosen_name": chosen_name,
        "target_pos": target_pos,
        "correct": correct,
        "raw_response": raw[:100],
    }


# ── Main Benchmark Loop ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Phase 4: Large-scale routing benchmark"
    )
    parser.add_argument("--skills", default=str(RESULTS_DIR / "selected_skills.json"))
    parser.add_argument("--queries", default=str(RESULTS_DIR / "benchmark_queries.json"))
    parser.add_argument("--repairs", default=str(RESULTS_DIR / "benchmark_repairs.json"))
    parser.add_argument("--routers", nargs="+",
                       default=list(ROUTERS.keys()),
                       help="Which routers to use")
    parser.add_argument("--conditions", nargs="+",
                       default=CONDITIONS,
                       help="Which description conditions to test")
    parser.add_argument("--limit", type=int, default=0,
                       help="Process only first N skills (0=all)")
    parser.add_argument("--smoke-test", action="store_true",
                       help="Run on 5 skills only")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Load data
    with open(args.skills) as f:
        skills = json.load(f)
    with open(args.queries) as f:
        queries = json.load(f)
    with open(args.repairs) as f:
        repairs = json.load(f)

    if args.smoke_test:
        skills = skills[:5]
        args.routers = args.routers[:1]
        print("[SMOKE TEST] 5 skills × 1 router")

    if args.limit:
        skills = skills[:args.limit]

    # Build distractor pool
    print("[Loading distractor pool...]")
    distractor_pool = build_distractor_pool(DB_PATH)
    print(f"  {len(distractor_pool)} distractors loaded")

    # Calculate total calls
    n_total = len(skills) * 8 * len(args.conditions) * len(args.routers)
    print(f"\n{'='*70}")
    print(f"ROUTING REPAIR BENCHMARK — Phase 4")
    print(f"{'='*70}")
    print(f"  Skills: {len(skills)}")
    print(f"  Queries per skill: 8")
    print(f"  Conditions: {args.conditions}")
    print(f"  Routers: {args.routers}")
    print(f"  Total calls: {n_total}")
    print(f"{'='*70}")

    # Load existing results for resume
    results_file = RESULTS_DIR / "routing_results.jsonl"
    existing = set()
    if results_file.exists():
        with open(results_file) as f:
            for line in f:
                r = json.loads(line)
                key = f"{r['skill_id']}|{r['condition']}|{r['router']}|{r['query']}"
                existing.add(key)
        print(f"[Resume] {len(existing)} existing results found")

    # Main loop
    completed = 0
    errors = 0
    t0 = time.time()

    with open(results_file, "a") as out_f:
        for si, skill in enumerate(skills):
            sid = skill["skill_id"]
            skill_queries = queries.get(sid, {}).get("queries", [])
            skill_repairs = repairs.get(sid, {})

            if not skill_queries:
                print(f"  [WARN] No queries for {skill['name']}, skipping")
                continue

            # Select distractors once per skill
            distractors = select_distractors(distractor_pool, sid)

            for router_key in args.routers:
                for condition in args.conditions:
                    for q in skill_queries:
                        query_text = q["query"]
                        key = f"{sid}|{condition}|{router_key}|{query_text}"
                        if key in existing:
                            completed += 1
                            continue

                        result = route_query(
                            query_text, skill, distractors,
                            condition, skill_repairs, router_key
                        )

                        record = {
                            "skill_id": sid,
                            "skill_name": skill["name"],
                            "ic_class": skill["ic_class"],
                            "query": query_text,
                            "query_type": q.get("query_type", "unknown"),
                            "expected_target": q.get("expected_target", True),
                            "condition": condition,
                            "router": router_key,
                            **result,
                        }

                        out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                        out_f.flush()
                        completed += 1

                        if result.get("raw_response", "").startswith("ERROR"):
                            errors += 1

            # Progress report
            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (n_total - completed) / rate if rate > 0 else 0
            print(f"  [{si+1}/{len(skills)}] {skill['name']}: "
                  f"{completed}/{n_total} done "
                  f"({rate:.1f}/s, ETA {eta/60:.0f}min, {errors} errors)")

    # Final summary
    print(f"\n{'='*70}")
    print(f"BENCHMARK COMPLETE")
    print(f"{'='*70}")
    print(f"  Total results: {completed}")
    print(f"  Errors: {errors}")
    print(f"  Time: {(time.time()-t0)/60:.1f} min")
    print(f"  Output: {results_file}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
