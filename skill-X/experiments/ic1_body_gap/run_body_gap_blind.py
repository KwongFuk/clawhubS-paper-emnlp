#!/usr/bin/env python3
"""
IC-1 Body Gap v2 — Name-Blind Queries
=======================================
Same 48 skills, but body_trigger queries that are NAME-BLIND:
they describe the hidden capability without ANY reference to the skill name.

This isolates the description's contribution from the name signal.
"""

import hashlib
import json
import os
import random
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


def llm_call(prompt: str, router: str = "openclaw") -> str:
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


def generate_blind_queries(skill_name: str, slug: str, description: str,
                           repair_sentence: str, router: str) -> list[str]:
    """Generate body_trigger queries that avoid the skill name entirely."""
    cache_key = hashlib.md5(f"blindv3_{slug}".encode()).hexdigest()[:16]
    cache_file = CACHE / f"blind_{cache_key}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except Exception:
            pass

    if not repair_sentence:
        return []

    # Extract keywords to ban
    name_words = set(slug.replace("-", " ").lower().split())
    name_words.update(skill_name.lower().split())
    banned = sorted(set(name_words) - {"a", "the", "and", "or", "to", "for"})

    prompt = (
        f"A software tool can: {repair_sentence} "
        f"Write 2 user requests asking it to do those things. "
        f"Do NOT use these words: {', '.join(banned)}. "
        f"Reply as a JSON array: [\"request1\", \"request2\"]"
    )

    text = llm_call(prompt, router)
    try:
        import re
        m = re.search(r'\[.*\]', text, re.DOTALL)
        if m:
            queries = json.loads(m.group())
            if isinstance(queries, list) and len(queries) >= 2:
                cache_file.write_text(json.dumps(queries[:2]))
                return queries[:2]
    except Exception:
        pass
    return []


def route_xml(query: str, library: list[dict], router: str = "openclaw") -> str:
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
    cache_file = CACHE / f"blind_route_{cache_key}.txt"
    if cache_file.exists():
        return cache_file.read_text().strip()

    text = llm_call(prompt, router)
    chosen = text.strip().lower().strip('"').strip("'")
    if chosen:
        cache_file.write_text(chosen)
    return chosen


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-skills", type=int, default=50)
    parser.add_argument("--n-distractors", type=int, default=20)
    parser.add_argument("--router", default="openclaw", choices=["openclaw", "gemini"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--phase", default="all", choices=["generate", "route", "all"])
    args = parser.parse_args()
    random.seed(args.seed)

    # Load existing skills
    queries_data = json.loads((REPAIR_DIR / "benchmark_queries.json").read_text())
    repairs_data = json.loads((REPAIR_DIR / "benchmark_repairs.json").read_text())

    # Build focal list (IC-1s only)
    focal = []
    for sid, q_data in queries_data.items():
        if q_data.get("ic_class") != "IC-1s" or sid not in repairs_data:
            continue
        r = repairs_data[sid]
        bt = [q for q in q_data["queries"] if q["query_type"] == "body_trigger"]
        dt = [q for q in q_data["queries"] if q["query_type"] == "desc_trigger"]
        if len(bt) < 2 or len(dt) < 2:
            continue
        focal.append({
            "skill_id": sid,
            "slug": sid.split("/")[-1],
            "name": q_data["name"],
            "original": r["original"],
            "improved": r["minimal_repair"],
            "repair_sentence": r.get("repair_sentence", ""),
            "desc_trigger": [q["query"] for q in dt[:2]],
            "body_trigger": [q["query"] for q in bt[:2]],
        })
        if len(focal) >= args.n_skills:
            break

    focal_ids = {s["skill_id"] for s in focal}
    distractors = []
    for sid, q_data in queries_data.items():
        if sid not in focal_ids and sid in repairs_data:
            distractors.append({
                "slug": sid.split("/")[-1],
                "description": repairs_data[sid]["original"],
            })

    # Phase 1: Generate blind queries
    blind_file = RESULTS / "blind_queries.json"
    if args.phase in ("generate", "all"):
        print(f"Generating name-blind queries for {len(focal)} skills...")
        for i, skill in enumerate(focal):
            print(f"  [{i+1}/{len(focal)}] {skill['name']}")
            blind = generate_blind_queries(
                skill["name"], skill["slug"], skill["original"],
                skill.get("repair_sentence", ""), args.router
            )
            skill["blind_trigger"] = blind
            if blind:
                print(f"    → {blind[0][:60]}...")
            else:
                print(f"    ✗ failed")
            time.sleep(0.5)
        blind_file.write_text(json.dumps(focal, indent=2, ensure_ascii=False))
        print(f"\n✅ Saved → {blind_file}")

    # Phase 2: Route
    if args.phase in ("route", "all"):
        if blind_file.exists():
            focal = json.loads(blind_file.read_text())

        print(f"\n{'='*65}")
        print(f"Routing: {len(focal)} skills × 2 variants × 3 query types")
        print(f"{'='*65}")

        all_rows = []
        for i, skill in enumerate(focal):
            if not skill.get("blind_trigger"):
                continue
            print(f"\n[{i+1}/{len(focal)}] {skill['name']} ({skill['slug']})")
            dist = random.sample(distractors, min(args.n_distractors, len(distractors)))

            for variant in ["original", "improved"]:
                desc = skill[variant]
                library = [{"slug": skill["slug"], "description": desc}] + dist
                random.shuffle(library)

                for q_type in ["desc_trigger", "body_trigger", "blind_trigger"]:
                    qs = skill.get(q_type, [])
                    for q in qs[:2]:
                        chosen = route_xml(q, library, router=args.router)
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

        # Summary
        out = RESULTS / f"body_gap_blind_{args.router}.json"
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

        # Key comparison
        for qt in ["body_trigger", "blind_trigger"]:
            orig = [r for r in all_rows if r["variant"] == "original" and r["query_type"] == qt]
            impr = [r for r in all_rows if r["variant"] == "improved" and r["query_type"] == qt]
            if orig and impr:
                o_rec = sum(r["triggered"] for r in orig) / len(orig) * 100
                i_rec = sum(r["triggered"] for r in impr) / len(impr) * 100
                print(f"\n  ★ {qt}: original={o_rec:.1f}% → improved={i_rec:.1f}% (Δ={i_rec-o_rec:+.1f}pp)")

        print(f"\n→ {out}")


if __name__ == "__main__":
    main()
