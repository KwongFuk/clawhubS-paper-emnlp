#!/usr/bin/env python3
"""
IC-1 Body Gap v3 — Template-based Name-Blind Queries
=====================================================
Instead of using LLM to generate blind queries (which gets safety-filtered),
directly construct them from repair_sentence by removing skill-name keywords.

3 query types per skill per variant:
  - desc_trigger: matches original description (control)
  - body_trigger: matches hidden capabilities (current baseline) 
  - blind_trigger: repair_sentence turned into user request, name removed
"""

import hashlib
import json
import random
import re
import subprocess
import time
from pathlib import Path

BASE = Path(__file__).parent
REPAIR_DIR = Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/routing_repair/results")
RESULTS = BASE / "results"
CACHE = BASE / ".cache"
RESULTS.mkdir(exist_ok=True)
CACHE.mkdir(exist_ok=True)

OPENCLAW = "openclaw"


def llm_call(prompt: str) -> str:
    for attempt in range(3):
        try:
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


def make_blind_queries(slug: str, name: str, repair_sentence: str) -> list[str]:
    """Convert repair_sentence into user-request style queries,
    removing any reference to the skill name."""
    if not repair_sentence:
        return []

    # Words to remove from queries
    name_words = set(slug.replace("-", " ").lower().split())
    name_words.update(name.lower().split())
    name_words -= {"a", "the", "and", "or", "to", "for", "of", "in", "on", "with", "is", "can"}

    cap = repair_sentence.strip().rstrip(".")

    # Remove skill name words (replace with generic term)
    for word in sorted(name_words, key=len, reverse=True):
        if len(word) < 3:
            continue
        cap = re.sub(r'\b' + re.escape(word) + r'\b', 'this tool', cap, flags=re.IGNORECASE)

    # Deduplicate "this tool this tool"
    cap = re.sub(r'(this tool\s*){2,}', 'this tool ', cap)

    # Two request framings
    queries = [
        f"I need the following capability: {cap}. Please help me set this up.",
        f"My requirement is: {cap}. Can you get this working for me?",
    ]
    return queries


def route_xml(query: str, library: list[dict]) -> str:
    skill_lines = []
    for s in library:
        skill_lines.append("  <skill>")
        skill_lines.append(f"    <name>{s['slug']}</name>")
        skill_lines.append(f"    <description>{s['description']}</description>")
        skill_lines.append(f"    <location>/skills/{s['slug']}/SKILL.md</location>")
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

    cache_key = hashlib.md5(prompt.encode()).hexdigest()[:16]
    cache_file = CACHE / f"bv3_route_{cache_key}.txt"
    if cache_file.exists():
        return cache_file.read_text().strip()

    text = llm_call(prompt)
    chosen = text.strip().lower().strip('"').strip("'")
    if chosen:
        cache_file.write_text(chosen)
    return chosen


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-skills", type=int, default=50)
    parser.add_argument("--n-distractors", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)

    # Load existing data
    queries_data = json.loads((REPAIR_DIR / "benchmark_queries.json").read_text())
    repairs_data = json.loads((REPAIR_DIR / "benchmark_repairs.json").read_text())

    # Build focal list
    focal = []
    for sid, q_data in queries_data.items():
        if q_data.get("ic_class") != "IC-1s" or sid not in repairs_data:
            continue
        r = repairs_data[sid]
        bt = [q for q in q_data["queries"] if q["query_type"] == "body_trigger"][:2]
        dt = [q for q in q_data["queries"] if q["query_type"] == "desc_trigger"][:2]
        rs = r.get("repair_sentence", "")
        if len(bt) < 2 or len(dt) < 2 or not rs:
            continue

        slug = sid.split("/")[-1]
        blind = make_blind_queries(slug, q_data["name"], rs)
        if not blind:
            continue

        focal.append({
            "skill_id": sid,
            "slug": slug,
            "name": q_data["name"],
            "original": r["original"],
            "improved": r["minimal_repair"],
            "repair_sentence": rs,
            "desc_trigger": [q["query"] for q in dt],
            "body_trigger": [q["query"] for q in bt],
            "blind_trigger": blind,
        })
        if len(focal) >= args.n_skills:
            break

    # Build distractor pool
    focal_ids = {s["skill_id"] for s in focal}
    distractors = []
    for sid, q_data in queries_data.items():
        if sid not in focal_ids and sid in repairs_data:
            distractors.append({
                "slug": sid.split("/")[-1],
                "description": repairs_data[sid]["original"],
            })

    print(f"{'='*65}")
    print(f"IC-1 Body Gap v3 — Template Blind Queries")
    print(f"{len(focal)} skills × 2 variants × 3 query types × 2 queries")
    print(f"{'='*65}")

    # Show sample blind queries
    for s in focal[:3]:
        print(f"\n  {s['slug']}:")
        print(f"    repair: {s['repair_sentence'][:80]}")
        print(f"    blind1: {s['blind_trigger'][0][:80]}")

    all_rows = []
    for i, skill in enumerate(focal):
        print(f"\n[{i+1}/{len(focal)}] {skill['name']} ({skill['slug']})")
        dist = random.sample(distractors, min(args.n_distractors, len(distractors)))

        for variant in ["original", "improved"]:
            desc = skill[variant]
            library = [{"slug": skill["slug"], "description": desc}] + dist
            random.shuffle(library)

            for q_type in ["desc_trigger", "body_trigger", "blind_trigger"]:
                for q in skill[q_type][:2]:
                    chosen = route_xml(q, library)
                    triggered = skill["slug"] in chosen
                    status = "✓" if triggered else "✗"
                    print(f"  [{variant:<10}] {q_type:<14} {status} → {chosen:<20} q={q[:45]}")
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

    # Save & Summary
    out = RESULTS / "body_gap_blind_v3.json"
    with open(out, "w") as f:
        json.dump(all_rows, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*65}")
    print("RESULTS")
    print(f"{'='*65}")
    print(f"\n{'Variant':<12} {'Query type':<16} {'Recall':>8}  n")
    print("-" * 55)
    for variant in ["original", "improved"]:
        for q_type in ["desc_trigger", "body_trigger", "blind_trigger"]:
            rows = [r for r in all_rows
                    if r["variant"] == variant and r["query_type"] == q_type]
            if not rows:
                continue
            rec = sum(r["triggered"] for r in rows) / len(rows) * 100
            print(f"  {variant:<10} {q_type:<16} {rec:>7.1f}%  {len(rows)}")

    for qt in ["body_trigger", "blind_trigger"]:
        orig = [r for r in all_rows if r["variant"] == "original" and r["query_type"] == qt]
        impr = [r for r in all_rows if r["variant"] == "improved" and r["query_type"] == qt]
        if orig and impr:
            o_rec = sum(r["triggered"] for r in orig) / len(orig) * 100
            i_rec = sum(r["triggered"] for r in impr) / len(impr) * 100
            print(f"\n  ★ {qt}: original={o_rec:.1f}% → improved={i_rec:.1f}% (Δ={i_rec-o_rec:+.1f}pp)")

    print(f"\n→ {out} ({len(all_rows)} decisions)")


if __name__ == "__main__":
    main()
