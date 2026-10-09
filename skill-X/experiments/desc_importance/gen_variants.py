#!/usr/bin/env python3
"""
Description Variant Generator (LLM-based)
==========================================
For each skill, generate 9 description variants via LLM:

  Delete (reduce information):
    del_80 — keep ~80% of information (remove least important 20%)
    del_60 — keep ~60% of information
    del_40 — keep ~40% of information

  Modify (vague-ify specific content):
    mod_40 — make ~40% of specific terms/numbers vague
    mod_60 — make ~60% vague
    mod_80 — make ~80% vague

  Add (enrich from body):
    add_40 — add ~40% more content from body
    add_60 — add ~60% more content from body
    add_80 — add ~80% more content from body

  + original (baseline, 100%)

Total: 50 skills × 10 variants saved for routing experiment
"""

import json, subprocess, time
from pathlib import Path

OPENCLAW  = "openclaw"
CANDIDATES = Path(__file__).parent / "skill_candidates_60.json"
QUERIES    = Path(__file__).parent / "queries_for_review.json"
OUT        = Path(__file__).parent / "variants_generated.json"

# ── Prompts ───────────────────────────────────────────────────────────────────

PROMPTS = {
    # ── Delete ────────────────────────────────────────────────────────────────
    "del_80": """\
Rewrite the following skill description keeping approximately 80% of the information.
Remove the least important 20%: trim edge-case details, minor features, or redundant phrases.
Keep the core purpose and main capabilities intact.
Return ONLY the rewritten description, no extra text.

Description:
{description}""",

    "del_60": """\
Rewrite the following skill description keeping approximately 60% of the information.
Remove the least important 40%: drop secondary features, specific constraints, and advanced options.
Keep the essential purpose and primary capabilities.
Return ONLY the rewritten description, no extra text.

Description:
{description}""",

    "del_40": """\
Rewrite the following skill description keeping approximately 40% of the information.
Keep ONLY the core purpose and primary capability — remove everything else.
Return ONLY the rewritten description, no extra text.

Description:
{description}""",

    # ── Modify ────────────────────────────────────────────────────────────────
    "mod_40": """\
Rewrite the following skill description, making roughly 40% of the specific content more vague.
Replace some specific numbers, technical tool names, and precise capability names with generic equivalents.
E.g., "50+ commands" → "many commands", "Chromium and Firefox" → "multiple browsers".
Keep 60% of the description's specificity unchanged.
Return ONLY the rewritten description, no extra text.

Description:
{description}""",

    "mod_60": """\
Rewrite the following skill description, making roughly 60% of the specific content more vague.
Replace most specific numbers, technical names, and precise capability names with generic equivalents.
Keep about 40% of the original specificity.
Return ONLY the rewritten description, no extra text.

Description:
{description}""",

    "mod_80": """\
Rewrite the following skill description, making roughly 80% of the specific content more vague.
Replace nearly all specific numbers, technical tool names, and precise capability details with generic language.
Keep only the most essential technical identity.
Return ONLY the rewritten description, no extra text.

Description:
{description}""",

    # ── Add ───────────────────────────────────────────────────────────────────
    "add_40": """\
Extend the following skill description by adding approximately 40% more content \
(relative to its current length: {desc_len} chars, so add ~{add_chars} chars).
Use the skill body below to find the most useful MISSING capabilities to add.
Write new sentences that naturally extend the description — do not repeat what's already there.
Return ONLY the complete extended description (original + additions), no extra text.

Description:
{description}

Skill body (use this as source for additions):
{body_excerpt}""",

    "add_60": """\
Extend the following skill description by adding approximately 60% more content \
(relative to its current length: {desc_len} chars, so add ~{add_chars} chars).
Use the skill body below to find the most useful MISSING capabilities or use-cases to add.
Write new sentences that naturally extend the description.
Return ONLY the complete extended description (original + additions), no extra text.

Description:
{description}

Skill body (use this as source for additions):
{body_excerpt}""",

    "add_80": """\
Extend the following skill description by adding approximately 80% more content \
(relative to its current length: {desc_len} chars, so add ~{add_chars} chars).
Use the skill body below to find all significant MISSING capabilities, trigger conditions, \
or use-cases to add.
Return ONLY the complete extended description (original + additions), no extra text.

Description:
{description}

Skill body (use this as source for additions):
{body_excerpt}""",
}

ADD_RATIOS = {"add_40": 0.4, "add_60": 0.6, "add_80": 0.8}

# ── LLM call ─────────────────────────────────────────────────────────────────

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
            print(f"    [WARN] timeout attempt {attempt+1}/3, retrying...")
            time.sleep(5)
        except Exception as e:
            print(f"    [ERR] {e}")
    return None


def generate_variants(slug: str, description: str, body: str) -> dict:
    desc_len = len(description)
    body_excerpt = body[:2000]
    variants = {"original": description}

    for v_name, tmpl in PROMPTS.items():
        if v_name.startswith("add_"):
            ratio = ADD_RATIOS[v_name]
            add_chars = int(desc_len * ratio)
            prompt = tmpl.format(
                description=description,
                desc_len=desc_len,
                add_chars=add_chars,
                body_excerpt=body_excerpt,
            )
        else:
            prompt = tmpl.format(description=description)

        result = llm(prompt)
        if result:
            variants[v_name] = result
            print(f"    {v_name:<10}  {len(result):4d} chars  "
                  f"(orig={desc_len}, ratio={len(result)/desc_len:.2f})")
        else:
            print(f"    {v_name:<10}  FAILED")
            variants[v_name] = description  # fallback to original

        time.sleep(0.5)

    return variants


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    candidates = json.loads(CANDIDATES.read_text())
    # Deduplicate
    seen, unique = set(), []
    for c in candidates:
        if c['slug'] not in seen:
            seen.add(c['slug'])
            unique.append(c)

    print(f"Total unique candidates: {len(unique)}")
    print(f"Generating 9 variants × {len(unique)} skills = {9*len(unique)} LLM calls\n")

    results = []

    # Check if partial results already exist (resume)
    if OUT.exists():
        results = json.loads(OUT.read_text())
        done_slugs = {r['slug'] for r in results}
        unique = [s for s in unique if s['slug'] not in done_slugs]
        print(f"Resuming: {len(done_slugs)} done, {len(unique)} remaining\n")

    for i, s in enumerate(unique):
        slug = s['slug']
        desc = s['description']
        body = s['body']

        print(f"[{i+1}/{len(unique)}] {slug}  (desc={len(desc)}, body={len(body)})")

        variants = generate_variants(slug, desc, body)

        results.append({
            "slug":        slug,
            "description": desc,
            "body_len":    len(body),
            "variants":    variants,
        })

        # Save after each skill (resume-safe)
        OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    print(f"\n✓ Done. {len(results)} skills → {OUT}")


if __name__ == "__main__":
    main()
