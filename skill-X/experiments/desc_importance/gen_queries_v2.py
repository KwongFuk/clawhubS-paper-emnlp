#!/usr/bin/env python3
"""
Query Generator v2
==================
For each skill, LLM generates 10 evaluation queries:

  goal_based    (3): user describes what they want to ACHIEVE — no tool name
  symptom_based (3): user describes a PROBLEM or pain point they have
  context_based (2): user gives background context, asks what to use/do
  boundary      (2): adjacent task that should NOT trigger this skill

Output: queries_v2.json — review before full experiment
"""

import json, subprocess, time
from pathlib import Path

OPENCLAW   = "openclaw"
CANDIDATES = Path(__file__).parent / "skill_candidates_60.json"
OUT        = Path(__file__).parent / "queries_v2.json"
N_SKILLS   = 30   # generate for first 30 unique skills

PROMPT = """\
You are designing routing evaluation queries for an AI skill system.

SKILL NAME: {slug}

SKILL DESCRIPTION:
{description}

SKILL BODY (first 1500 chars):
{body_excerpt}

Generate exactly 10 realistic user queries for this skill. \
Do NOT mention the skill name or tool name in goal_based, symptom_based, or context_based queries.

Return ONLY valid JSON:
{{
  "goal_based": [
    "query describing what the user wants to achieve (no tool name)",
    "another goal-oriented query",
    "third goal-oriented query"
  ],
  "symptom_based": [
    "query describing a problem or pain point the user has",
    "another problem/symptom query",
    "third symptom query"
  ],
  "context_based": [
    "query where user gives background context and asks what to use or how to proceed",
    "another context-rich query"
  ],
  "boundary": [
    "query for an adjacent task that looks similar but should use a DIFFERENT skill",
    "another near-miss query for a different but related tool or domain"
  ]
}}

Rules:
- goal_based: pure user intent — "I want to...", "I need to...", "Help me..."
- symptom_based: user describes friction/pain — "My X keeps failing", "I can't figure out how to...", "Every time I try to..."
- context_based: user provides scenario context — "I'm building a system that...", "In my project, I need to...", "We're a team that..."
- boundary: pick adjacent domain (same broad category but different tool/platform/scope)
- All queries must be 1-3 natural sentences, realistic user messages
- Return ONLY the JSON object, no explanation or markdown"""


def generate(slug: str, description: str, body: str) -> dict | None:
    prompt = PROMPT.format(
        slug=slug,
        description=description,
        body_excerpt=body[:1500],
    )
    for attempt in range(3):
        try:
            r = subprocess.run(
                [OPENCLAW, "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120,
            )
            j = json.loads(r.stdout)
            text = j.get("outputs", [{}])[0].get("text", "").strip()
            # Strip markdown fences if present
            if "```" in text:
                parts = text.split("```")
                for p in parts:
                    p = p.strip()
                    if p.startswith("json"):
                        p = p[4:].strip()
                    try:
                        return json.loads(p)
                    except Exception:
                        continue
            return json.loads(text)
        except subprocess.TimeoutExpired:
            print(f"    [WARN] timeout attempt {attempt+1}/3")
            time.sleep(5)
        except json.JSONDecodeError as e:
            print(f"    [WARN] JSON parse failed: {e}")
            time.sleep(2)
        except Exception as e:
            print(f"    [ERR] {e}")
    return None


def main():
    candidates = json.loads(CANDIDATES.read_text())
    seen, unique = set(), []
    for c in candidates:
        if c["slug"] not in seen:
            seen.add(c["slug"])
            unique.append(c)

    # Resume if partial output exists
    results = []
    done_slugs = set()
    if OUT.exists():
        results = json.loads(OUT.read_text())
        done_slugs = {r["slug"] for r in results}

    todo = [s for s in unique[:N_SKILLS] if s["slug"] not in done_slugs]
    print(f"Skills to generate: {len(todo)}  (already done: {len(done_slugs)})\n")

    for i, s in enumerate(todo):
        slug = s["slug"]
        desc = s["description"]
        body = s["body"]

        print(f"[{i+1}/{len(todo)}] {slug}")
        print(f"  desc ({len(desc)} chars): {desc[:70]}...")

        queries = generate(slug, desc, body)
        if queries is None:
            print("  [SKIP] failed to generate\n")
            continue

        n = (len(queries.get("goal_based", [])) +
             len(queries.get("symptom_based", [])) +
             len(queries.get("context_based", [])) +
             len(queries.get("boundary", [])))
        print(f"  ✓ {n} queries generated")

        for qt, qs in queries.items():
            for q in qs:
                print(f"    [{qt:<14}] {q[:75]}")
        print()

        results.append({
            "slug": slug,
            "description": desc,
            "desc_len": len(desc),
            "body_len": len(body),
            "queries": queries,
        })
        OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        time.sleep(0.5)

    print(f"\n✓ Done. {len(results)} skills × 10 queries → {OUT}")
    print(f"\n*** REVIEW {OUT.name} BEFORE RUNNING FULL EXPERIMENT ***")


if __name__ == "__main__":
    main()
