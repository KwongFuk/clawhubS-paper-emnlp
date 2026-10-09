#!/usr/bin/env python3
"""
Step 1: Auto-generate queries for N skills (for human review)
============================================================
For each skill, LLM generates 6 queries:
  direct   (2): user explicitly names the tool/domain → should trigger
  indirect (2): user describes the need without tool name → should trigger
  boundary (2): adjacent task, should NOT trigger this skill

Output: queries_for_review.json — human reviews before full batch run
"""

import json, subprocess, time
from pathlib import Path

OPENCLAW = "openclaw"
CANDIDATES = Path(__file__).parent / "skill_candidates_60.json"
OUT        = Path(__file__).parent / "queries_for_review.json"

# How many skills to generate for review
N_REVIEW = 5

PROMPT_TMPL = """\
You are helping design a routing evaluation for an AI skill system.

SKILL NAME: {slug}

SKILL DESCRIPTION (what the routing agent sees):
{description}

SKILL BODY EXCERPT (full capabilities, first 1500 chars):
{body_excerpt}

Generate exactly 6 evaluation queries for this skill. Each query should be a realistic \
user message (1-2 sentences, natural language).

Return ONLY valid JSON in this exact format:
{{
  "direct": [
    "query 1 — explicitly mentions the tool/domain name or key technical term",
    "query 2 — another direct reference"
  ],
  "indirect": [
    "query 3 — describes what the user needs without naming the tool (routing depends on description quality)",
    "query 4 — another indirect, task-focused query"
  ],
  "boundary": [
    "query 5 — an adjacent task that looks similar but should NOT route to this skill",
    "query 6 — another near-miss that should use a different skill"
  ]
}}

Rules:
- Queries must be realistic things a real user would ask
- direct queries: mention the tool name or its main technical term
- indirect queries: describe the user's GOAL, not the tool — these are the key test cases
- boundary queries: pick an adjacent domain (e.g., if skill is about CLI, boundary = web UI of same tool)
- Do NOT include phrases like "use the skill" or "invoke the skill"
- Return ONLY the JSON object, no other text"""


def generate_queries(slug: str, description: str, body: str) -> dict | None:
    body_excerpt = body[:1500]
    prompt = PROMPT_TMPL.format(
        slug=slug,
        description=description,
        body_excerpt=body_excerpt,
    )
    for attempt in range(3):
        try:
            r = subprocess.run(
                [OPENCLAW, "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120,
            )
            j = json.loads(r.stdout)
            text = j.get("outputs", [{}])[0].get("text", "").strip()
            # Parse JSON from response
            # Sometimes LLM wraps in ```json ... ```
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text.strip())
        except subprocess.TimeoutExpired:
            print(f"  [WARN] timeout attempt {attempt+1}/3")
            time.sleep(5)
        except json.JSONDecodeError as e:
            print(f"  [WARN] JSON parse failed attempt {attempt+1}: {e}")
            print(f"  Response: {text[:200] if 'text' in dir() else 'N/A'}")
            time.sleep(2)
        except Exception as e:
            print(f"  [ERR] {e}")
    return None


def main():
    candidates = json.loads(CANDIDATES.read_text())
    # Deduplicate by slug
    seen = set()
    unique = []
    for c in candidates:
        if c['slug'] not in seen:
            seen.add(c['slug'])
            unique.append(c)

    print(f"Unique candidates: {len(unique)}")
    print(f"Generating queries for first {N_REVIEW} skills...\n")

    results = []
    for s in unique[:N_REVIEW]:
        slug = s['slug']
        desc = s['description']
        body = s['body']

        print(f"[{slug}]")
        print(f"  desc ({len(desc)} chars): {desc[:80]}...")

        queries = generate_queries(slug, desc, body)
        if queries is None:
            print(f"  [SKIP] Failed to generate queries")
            continue

        entry = {
            "slug": slug,
            "description": desc,
            "desc_len": len(desc),
            "body_len": len(body),
            "queries": queries,
        }
        results.append(entry)

        # Print for human review
        print(f"  DIRECT:")
        for q in queries.get("direct", []):
            print(f"    ✓ {q}")
        print(f"  INDIRECT:")
        for q in queries.get("indirect", []):
            print(f"    ~ {q}")
        print(f"  BOUNDARY:")
        for q in queries.get("boundary", []):
            print(f"    ✗ {q}")
        print()

        time.sleep(1)

    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\n→ Saved to {OUT}")
    print(f"  {len(results)} skills × 6 queries = {len(results)*6} total")
    print(f"\n  ** REVIEW {OUT.name} BEFORE RUNNING FULL EXPERIMENT **")


if __name__ == "__main__":
    main()
