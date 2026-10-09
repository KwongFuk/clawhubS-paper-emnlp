#!/usr/bin/env python3
"""
IC-1 Body Gap — 50-skill XML Routing Experiment
=================================================
Replicates the pilot IC-1 design at 50-skill scale using:
- 50 IC-1s skills from routing_repair data (already have queries + repairs)
- XML routing prompt (same as real OpenClaw routing)
- 2 variants: original vs minimal_repair
- 2 query types: desc_trigger vs body_trigger
- 20 distractors per routing call

Total: 50 skills × 2 variants × 4 queries = 400 routing decisions
"""

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
REPAIR_DIR = Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/routing_repair/results")
RESULTS = BASE / "results"
CACHE = BASE / ".cache"
RESULTS.mkdir(exist_ok=True)
CACHE.mkdir(exist_ok=True)

OPENCLAW = "openclaw"


def load_skills_and_queries():
    """Load IC-1s skills with queries and repair descriptions."""
    queries = json.loads((REPAIR_DIR / "benchmark_queries.json").read_text())
    repairs = json.loads((REPAIR_DIR / "benchmark_repairs.json").read_text())

    skills = []
    for sid, q_data in queries.items():
        if q_data.get("ic_class") != "IC-1s":
            continue
        if sid not in repairs:
            continue

        r = repairs[sid]
        dt = [q for q in q_data["queries"] if q["query_type"] == "desc_trigger"][:2]
        bt = [q for q in q_data["queries"] if q["query_type"] == "body_trigger"][:2]

        if len(dt) < 2 or len(bt) < 2:
            continue

        skills.append({
            "skill_id": sid,
            "slug": sid.split("/")[-1],
            "name": q_data["name"],
            "original": r["original"],
            "improved": r["minimal_repair"],
            "desc_trigger": [q["query"] for q in dt],
            "body_trigger": [q["query"] for q in bt],
        })

    return skills


def load_distractors(exclude_ids: set):
    """Load distractor skills from the full query set."""
    queries = json.loads((REPAIR_DIR / "benchmark_queries.json").read_text())
    repairs = json.loads((REPAIR_DIR / "benchmark_repairs.json").read_text())

    distractors = []
    for sid, q_data in queries.items():
        if sid in exclude_ids:
            continue
        if sid in repairs:
            distractors.append({
                "slug": sid.split("/")[-1],
                "description": repairs[sid]["original"],
            })
    return distractors


def llm_call(prompt: str, router: str = "openclaw") -> str:
    """Call LLM via openclaw or gemini CLI."""
    for attempt in range(3):
        try:
            if router == "gemini":
                result = subprocess.run(
                    ["gemini", "-p", prompt, "-m", "gemini-2.5-flash"],
                    capture_output=True, text=True, timeout=120,
                    stdin=subprocess.DEVNULL,
                )
                lines = [l.strip() for l in result.stdout.strip().split("\n")
                         if l.strip() and not l.startswith("Warning:")
                         and not l.startswith("Ripgrep")]
                return "\n".join(lines) if lines else ""
            else:
                result = subprocess.run(
                    [OPENCLAW, "infer", "model", "run",
                     "--gateway", "--prompt", prompt, "--json"],
                    capture_output=True, text=True, timeout=120,
                    stdin=subprocess.DEVNULL,
                )
                j = json.loads(result.stdout)
                return j.get("outputs", [{}])[0].get("text", "").strip()
        except subprocess.TimeoutExpired:
            time.sleep((attempt + 1) * 3)
        except Exception as e:
            if attempt == 2:
                print(f"  [ERR] {e}")
    return ""


def route_xml(query: str, library: list[dict], router: str = "openclaw") -> str:
    """Route query using XML skill format (matching real OpenClaw routing)."""
    skill_lines = []
    for s in library:
        slug = s["slug"]
        desc = s["description"]
        skill_lines.append("  <skill>")
        skill_lines.append(f"    <name>{slug}</name>")
        skill_lines.append(f"    <description>{desc}</description>")
        skill_lines.append(f"    <location>/skills/{slug}/SKILL.md</location>")
        skill_lines.append("  </skill>")

    prompt = (
        "The following skills provide specialized instructions for specific tasks.\n"
        "Use the read tool to load a skill's file when the task matches its description.\n\n"
        "<available_skills>\n"
        + "\n".join(skill_lines)
        + "\n</available_skills>\n\n"
        f"User request: {query}\n\n"
        "Which single skill would you load with the read tool?\n"
        'Reply with the skill slug only (e.g. "task-runner"), or "none" if no skill applies.'
    )

    # Cache
    cache_key = hashlib.md5(prompt.encode()).hexdigest()[:16]
    cache_file = CACHE / f"xmlroute_{cache_key}.txt"
    if cache_file.exists():
        return cache_file.read_text().strip()

    text = llm_call(prompt, router)
    chosen = text.strip().lower().strip('"').strip("'")

    if chosen:
        cache_file.write_text(chosen)
    return chosen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-skills", type=int, default=50)
    parser.add_argument("--n-distractors", type=int, default=20)
    parser.add_argument("--router", default="openclaw", choices=["openclaw", "gemini"])
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    # Load data
    all_skills = load_skills_and_queries()
    print(f"Available IC-1s skills: {len(all_skills)}")

    # Select 50
    focal = all_skills[:args.n_skills]
    focal_ids = {s["skill_id"] for s in focal}
    all_distractors = load_distractors(focal_ids)
    print(f"Focal: {len(focal)}, Distractors available: {len(all_distractors)}")

    print(f"\n{'=' * 65}")
    print(f"IC-1 Body Gap Experiment")
    print(f"{len(focal)} skills × 2 variants × 4 queries = {len(focal)*8} decisions")
    print(f"Router: {args.router}")
    print(f"{'=' * 65}")

    all_rows = []
    for i, skill in enumerate(focal):
        print(f"\n[{i+1}/{len(focal)}] {skill['name']} ({skill['slug']})")

        # Random distractors for this skill
        dist = random.sample(all_distractors, min(args.n_distractors, len(all_distractors)))

        for variant in ["original", "improved"]:
            desc = skill[variant]
            library = [{"slug": skill["slug"], "description": desc}] + dist

            # Shuffle library to avoid position bias
            random.shuffle(library)

            for q_type in ["desc_trigger", "body_trigger"]:
                for q in skill[q_type]:
                    chosen = route_xml(q, library, router=args.router)
                    triggered = skill["slug"] in chosen
                    status = "✓" if triggered else "✗"
                    print(f"  [{variant:<10}] {q_type:<13} {status}  "
                          f"chosen={chosen:<25}  q={q[:50]}")

                    all_rows.append({
                        "skill_id": skill["skill_id"],
                        "skill": skill["slug"],
                        "variant": variant,
                        "query_type": q_type,
                        "query": q,
                        "chosen": chosen,
                        "triggered": triggered,
                    })
                    time.sleep(0.3)

    # ── Save & Summary ──────────────────────────────────────────────────────
    out = RESULTS / f"body_gap_{args.router}.json"
    with open(out, "w") as f:
        json.dump(all_rows, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 65}")
    print("RESULTS")
    print(f"{'=' * 65}")

    print(f"\n{'Variant':<12}  {'Query type':<15}  {'Recall':>8}  n")
    print("-" * 50)
    for variant in ["original", "improved"]:
        for q_type in ["desc_trigger", "body_trigger"]:
            rows = [r for r in all_rows
                    if r["variant"] == variant and r["query_type"] == q_type]
            rec = sum(r["triggered"] for r in rows) / len(rows) * 100 if rows else 0
            print(f"  {variant:<10}  {q_type:<15}  {rec:>7.1f}%  {len(rows)}")

    # Key metric
    orig_bt = [r for r in all_rows if r["variant"] == "original" and r["query_type"] == "body_trigger"]
    impr_bt = [r for r in all_rows if r["variant"] == "improved" and r["query_type"] == "body_trigger"]
    orig_rec = sum(r["triggered"] for r in orig_bt) / len(orig_bt) * 100 if orig_bt else 0
    impr_rec = sum(r["triggered"] for r in impr_bt) / len(impr_bt) * 100 if impr_bt else 0
    print(f"\n  ★ body_trigger recall:  original={orig_rec:.1f}% → improved={impr_rec:.1f}%  "
          f"(Δ={impr_rec - orig_rec:+.1f}pp)")
    print(f"\n→ {out}  ({len(all_rows)} routing decisions)")


if __name__ == "__main__":
    main()
