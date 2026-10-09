#!/usr/bin/env python3
"""
generate_queries_and_repairs.py — Phase 2+3: Query Generation & Description Repairs
====================================================================================
For each selected skill:
1. Generate 8 routing test queries (desc-trigger, body-trigger, paraphrase, negative)
2. Generate 3 description conditions (minimal repair, length-matched control, structured)

Uses openclaw infer model run for LLM calls (no API key needed).
Checkpoint-safe: caches all LLM outputs.
"""
import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

SEED = 42
random.seed(SEED)

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
CACHE_DIR = BASE_DIR / ".cache_gen"

OPENCLAW_BIN = "openclaw"


# ── LLM Call ─────────────────────────────────────────────────────────────────

def llm_call(prompt: str, cache_key: str = "", retries: int = 2) -> str:
    """Run LLM via openclaw infer model run."""
    if cache_key:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cf = CACHE_DIR / f"{cache_key}.txt"
        if cf.exists():
            return cf.read_text().strip()

    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                [OPENCLAW_BIN, "infer", "model", "run",
                 "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120
            )
            output = result.stdout.strip()
            try:
                j = json.loads(output)
                text = j.get("outputs", [{}])[0].get("text", "").strip()
            except Exception:
                lines = [l.strip() for l in output.split("\n") if l.strip()]
                text = lines[-1] if lines else ""

            if cache_key:
                (CACHE_DIR / f"{cache_key}.txt").write_text(text)
            return text

        except subprocess.TimeoutExpired:
            wait = (attempt + 1) * 5
            print(f"  [WARN] Timeout (attempt {attempt+1}), retry in {wait}s")
            time.sleep(wait)

    if cache_key:
        (CACHE_DIR / f"{cache_key}.txt").write_text("ERROR")
    return "ERROR"


def parse_json_response(raw: str) -> dict | list | None:
    """Parse JSON from LLM response, handling markdown code blocks."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw).rstrip("`").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'[\[{].*[\]}]', raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except:
                pass
    return None


# ── Query Generation ─────────────────────────────────────────────────────────

QUERY_PROMPT = """\
You are generating test queries for evaluating skill routing accuracy.

Skill name: {name}
Skill description: {description}

The skill has these UNDISCLOSED capabilities (not mentioned in description):
{omitted_summary}

Generate exactly 8 test queries as a JSON array. Each query object has:
  "query": the user request (1-2 sentences, natural language)
  "query_type": one of "desc_trigger", "body_trigger", "paraphrase_body", "negative"
  "expected_target": true (this skill should be selected) or false (should NOT be selected)

Rules:
- 2 desc_trigger (expected_target=true): Ask for capabilities DESCRIBED in the listing.
  Example: if skill is "web search", ask "search the web for recent news about AI".
- 3 body_trigger (expected_target=true): Ask for capabilities NOT described but known
  to exist ({omitted_summary}). Use specific vocabulary.
  Example: if omitted="executes shell commands", ask "run a shell script to check disk usage".
- 2 paraphrase_body (expected_target=true): Same as body_trigger but paraphrased
  with synonyms. No direct vocabulary overlap with the omitted capability text.
  Example: if omitted="executes shell commands", ask "perform a system-level operation to
  see storage remaining".
- 1 negative (expected_target=false): A request that sounds related but needs a
  DIFFERENT skill entirely.

Output ONLY the JSON array, no markdown fences, no explanations."""


def generate_queries(skill: dict) -> list[dict]:
    """Generate 8 routing test queries for a skill."""
    ck = hashlib.sha256(
        (skill["skill_id"] + skill.get("omitted_summary", "")).encode()
    ).hexdigest()[:20]

    prompt = QUERY_PROMPT.format(
        name=skill["name"],
        description=skill["description"][:500],
        omitted_summary=skill.get("omitted_summary", "no omissions"),
    )

    raw = llm_call(prompt, cache_key=f"queries_{ck}")
    queries = parse_json_response(raw)

    if not queries or not isinstance(queries, list):
        print(f"  [WARN] Failed to parse queries for {skill['name']}")
        return []

    # Validate and normalize
    valid = []
    for q in queries:
        if isinstance(q, dict) and "query" in q and "query_type" in q:
            q.setdefault("expected_target", q.get("should_trigger", True))
            valid.append(q)

    return valid


# ── Repair Generation ────────────────────────────────────────────────────────

REPAIR_PROMPT = """\
You are writing a one-sentence disclosure for a skill listing.

Skill name: {name}
Current description: {description}

The skill has these UNDISCLOSED capabilities:
{omitted_summary}

Write ONE sentence (15-30 words) that discloses the most important omitted
capability. The sentence should:
1. Be factual and specific
2. Use natural language (not security jargon)
3. Fit naturally at the end of the existing description

Output ONLY the one sentence, no quotes, no explanation."""

LENGTH_CONTROL_PROMPT = """\
Write ONE sentence (15-30 words) about general software best practices.
The sentence should:
1. Be approximately {target_len} characters long
2. NOT mention any specific capabilities, APIs, or technical features
3. Sound professional but generic

Output ONLY the one sentence, no quotes."""

STRUCTURED_REPAIR_PROMPT = """\
You are creating a structured capability disclosure for a skill listing.

Skill name: {name}
Current description: {description}

The skill has these capabilities (some not currently disclosed):
{omitted_summary}

Create a structured disclosure in this format:
Capabilities: [2-3 bullet points of what the skill can do]
Permissions: [what access it requires: API keys, shell, files, etc.]
Limitations: [1 sentence about what it cannot do]

Output ONLY the structured disclosure, no other text."""


def generate_repairs(skill: dict) -> dict:
    """Generate 3 repair conditions for a skill."""
    ck = hashlib.sha256(skill["skill_id"].encode()).hexdigest()[:20]

    if skill["ic_class"] == "IC-0":
        # Control skills: no repair needed, just length-matched additions
        return {
            "original": skill["description"],
            "minimal_repair": skill["description"],  # Same (no omission)
            "length_control": skill["description"],
            "structured_repair": skill["description"],
        }

    # 1. Minimal repair
    raw_repair = llm_call(
        REPAIR_PROMPT.format(
            name=skill["name"],
            description=skill["description"][:400],
            omitted_summary=skill.get("omitted_summary", ""),
        ),
        cache_key=f"repair_{ck}",
    )
    minimal = skill["description"] + " " + raw_repair.strip()

    # 2. Length-matched control
    repair_len = len(raw_repair.strip())
    raw_control = llm_call(
        LENGTH_CONTROL_PROMPT.format(target_len=repair_len),
        cache_key=f"control_{ck}",
    )
    length_control = skill["description"] + " " + raw_control.strip()

    # 3. Structured repair
    raw_struct = llm_call(
        STRUCTURED_REPAIR_PROMPT.format(
            name=skill["name"],
            description=skill["description"][:400],
            omitted_summary=skill.get("omitted_summary", ""),
        ),
        cache_key=f"struct_{ck}",
    )
    structured = skill["description"] + "\n\n" + raw_struct.strip()

    return {
        "original": skill["description"],
        "minimal_repair": minimal,
        "length_control": length_control,
        "structured_repair": structured,
        "repair_sentence": raw_repair.strip(),
        "control_sentence": raw_control.strip(),
        "repair_len": len(raw_repair.strip()),
        "control_len": len(raw_control.strip()),
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Phase 2+3: Generate queries and description repairs"
    )
    parser.add_argument("--input", default=str(RESULTS_DIR / "selected_skills.json"),
                       help="Input: selected skills JSON")
    parser.add_argument("--queries-only", action="store_true",
                       help="Only generate queries, skip repairs")
    parser.add_argument("--repairs-only", action="store_true",
                       help="Only generate repairs, skip queries")
    parser.add_argument("--limit", type=int, default=0,
                       help="Process only first N skills (0=all)")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Load selected skills
    with open(args.input) as f:
        skills = json.load(f)

    if args.limit:
        skills = skills[:args.limit]

    print("=" * 70)
    print(f"ROUTING REPAIR — Phase 2+3: Queries & Repairs ({len(skills)} skills)")
    print("=" * 70)

    # Phase 2: Query Generation
    if not args.repairs_only:
        print(f"\n[Phase 2] Generating queries for {len(skills)} skills...")
        all_queries = {}
        total_q = 0

        for i, skill in enumerate(skills):
            queries = generate_queries(skill)
            all_queries[skill["skill_id"]] = {
                "name": skill["name"],
                "ic_class": skill["ic_class"],
                "queries": queries,
            }
            total_q += len(queries)

            if (i + 1) % 10 == 0:
                print(f"  [{i+1}/{len(skills)}] {total_q} queries generated")

        with open(RESULTS_DIR / "benchmark_queries.json", "w") as f:
            json.dump(all_queries, f, indent=2, ensure_ascii=False)
        print(f"[✓] {total_q} queries → {RESULTS_DIR / 'benchmark_queries.json'}")

        # Distribution check
        type_counts = {}
        for sid, sq in all_queries.items():
            for q in sq["queries"]:
                qt = q.get("query_type", "unknown")
                type_counts[qt] = type_counts.get(qt, 0) + 1
        print(f"  Query type distribution: {type_counts}")

    # Phase 3: Repair Generation
    if not args.queries_only:
        print(f"\n[Phase 3] Generating repairs for {len(skills)} skills...")
        all_repairs = {}

        for i, skill in enumerate(skills):
            repairs = generate_repairs(skill)
            all_repairs[skill["skill_id"]] = {
                "name": skill["name"],
                "ic_class": skill["ic_class"],
                **repairs,
            }

            if (i + 1) % 10 == 0:
                print(f"  [{i+1}/{len(skills)}] repairs generated")

            # Save checkpoint
            if (i + 1) % 25 == 0:
                with open(RESULTS_DIR / "benchmark_repairs_checkpoint.json", "w") as f:
                    json.dump(all_repairs, f, indent=2, ensure_ascii=False)

        with open(RESULTS_DIR / "benchmark_repairs.json", "w") as f:
            json.dump(all_repairs, f, indent=2, ensure_ascii=False)
        print(f"[✓] Repairs → {RESULTS_DIR / 'benchmark_repairs.json'}")

        # Length matching check
        ic1_repairs = {k: v for k, v in all_repairs.items()
                      if v["ic_class"] != "IC-0"}
        if ic1_repairs:
            repair_lens = [v.get("repair_len", 0) for v in ic1_repairs.values()]
            control_lens = [v.get("control_len", 0) for v in ic1_repairs.values()]
            avg_r = sum(repair_lens) / len(repair_lens) if repair_lens else 0
            avg_c = sum(control_lens) / len(control_lens) if control_lens else 0
            print(f"  Avg repair sentence: {avg_r:.0f} chars")
            print(f"  Avg control sentence: {avg_c:.0f} chars")

    print(f"\n{'='*70}")
    print("Phase 2+3 COMPLETE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
