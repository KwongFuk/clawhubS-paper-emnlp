#!/usr/bin/env python3
"""
Description Variant Generator v2
==================================
5 variants per skill:
  del_40   — LLM keeps ~40% of info (removes 60%)
  del_60   — LLM keeps ~60% of info (removes 40%)
  del_80   — LLM keeps ~80% of info (removes 20%)
  original — unchanged baseline
  complete — LLM adds all missing capabilities from body (補全)

Resume-safe: saves after each skill.
"""

import json, subprocess, time
from pathlib import Path

OPENCLAW   = "openclaw"
QUERIES    = Path(__file__).parent / "queries_v2.json"  # use skills from query file
OUT        = Path(__file__).parent / "variants_v2.json"

PROMPTS = {
    "del_40": """\
Rewrite the following skill description keeping approximately 40% of its information.
Remove the least important 60%: drop secondary features, specific constraints, advanced options, \
and detailed examples. Keep ONLY the essential core purpose and primary capability.
Write natural, coherent prose — not a fragment or truncation.
Return ONLY the rewritten description, no extra text.

Original description:
{description}""",

    "del_60": """\
Rewrite the following skill description keeping approximately 60% of its information.
Remove the least important 40%: trim detailed feature lists, edge-case options, and minor capabilities.
Keep the core purpose, main capabilities, and primary use cases.
Write natural, coherent prose.
Return ONLY the rewritten description, no extra text.

Original description:
{description}""",

    "del_80": """\
Rewrite the following skill description keeping approximately 80% of its information.
Remove the least important 20%: trim the most detailed or niche parts only.
Preserve the overall structure, main features, and key specifics.
Write natural, coherent prose.
Return ONLY the rewritten description, no extra text.

Original description:
{description}""",

    "complete": """\
You are improving a skill description by adding missing capabilities.

Current description:
{description}

Skill body (source of truth for what the skill can actually do):
{body_excerpt}

Task: Extend the description by adding all significant capabilities, use cases, and \
trigger conditions that are present in the body but NOT mentioned in the description.
Write natural prose that flows from the existing description.
Do NOT repeat what's already in the description.
Do NOT add capabilities that are not in the body.
Return ONLY the complete extended description (original + additions), no extra text.""",
}


def llm(prompt: str) -> str | None:
    for attempt in range(3):
        try:
            r = subprocess.run(
                [OPENCLAW, "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120,
            )
            j = json.loads(r.stdout)
            return j.get("outputs", [{}])[0].get("text", "").strip()
        except subprocess.TimeoutExpired:
            print(f"    [WARN] timeout attempt {attempt+1}/3")
            time.sleep(5)
        except Exception as e:
            print(f"    [ERR] {e}")
    return None


def generate_variants(slug: str, description: str, body: str) -> dict:
    variants = {"original": description}
    body_excerpt = body[:2000]

    for v_name, tmpl in PROMPTS.items():
        if v_name == "complete":
            prompt = tmpl.format(description=description, body_excerpt=body_excerpt)
        else:
            prompt = tmpl.format(description=description)

        result = llm(prompt)
        orig_len = len(description)

        if result:
            ratio = len(result) / orig_len
            variants[v_name] = result
            print(f"    {v_name:<10}  {len(result):5d} chars  "
                  f"ratio={ratio:.2f}  {'✓' if ratio > 0.1 else '!'}")
        else:
            print(f"    {v_name:<10}  FAILED — using original")
            variants[v_name] = description

        time.sleep(0.5)

    return variants


def main():
    # Load skills from queries file (already selected 30 skills)
    if not QUERIES.exists():
        print(f"ERROR: {QUERIES} not found. Run gen_queries_v2.py first.")
        return

    skill_data = json.loads(QUERIES.read_text())
    print(f"Skills from queries file: {len(skill_data)}")

    # Resume
    results = []
    done_slugs = set()
    if OUT.exists():
        results = json.loads(OUT.read_text())
        done_slugs = {r["slug"] for r in results}

    todo = [s for s in skill_data if s["slug"] not in done_slugs]
    print(f"Generating variants for {len(todo)} skills "
          f"({len(done_slugs)} already done)\n")

    for i, s in enumerate(todo):
        slug = s["slug"]
        desc = s["description"]
        # body stored in candidates file, not queries file
        # Load from candidates
        body = s.get("body", "")
        if not body:
            # Try loading from candidates
            cands_path = Path(__file__).parent / "skill_candidates_60.json"
            if cands_path.exists():
                cands = json.loads(cands_path.read_text())
                match = next((c for c in cands if c["slug"] == slug), None)
                body = match["body"] if match else ""

        print(f"[{i+1}/{len(todo)}] {slug}  (desc={len(desc)}, body={len(body)})")
        variants = generate_variants(slug, desc, body)

        results.append({
            "slug":        slug,
            "description": desc,
            "body_len":    len(body),
            "variants":    variants,
        })
        OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print()

    print(f"✓ Done. {len(results)} skills × 5 variants → {OUT}")


if __name__ == "__main__":
    main()
