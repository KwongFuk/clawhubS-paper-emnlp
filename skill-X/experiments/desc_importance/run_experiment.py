#!/usr/bin/env python3
"""
Description Quality Routing Experiment  (main experiment)
==========================================================
30 skills × 5 variants × 10 queries = 1500 routing decisions

For each skill + variant, build a routing library (focal skill + 19 distractors)
and test whether each query triggers the focal skill.

Metrics:
  Recall    = trigger rate on (goal_based + symptom_based + context_based) queries
  Precision = non-trigger rate on boundary queries

Expected result:
  Recall(del_40) < Recall(del_60) < Recall(del_80) < Recall(original) ≤ Recall(complete)

Output: results_experiment.json + summary table
"""

import json, random, subprocess, time
from pathlib import Path
from collections import defaultdict

OPENCLAW  = "openclaw"
QUERIES   = Path(__file__).parent / "queries_v2.json"
VARIANTS  = Path(__file__).parent / "variants_v2.json"
OUT       = Path(__file__).parent / "results_experiment.json"

VARIANT_ORDER = ["del_40", "del_60", "del_80", "original", "complete"]

# ── 19 fixed distractor skills ─────────────────────────────────────────────
# Diverse domains to ensure routing competition is realistic
DISTRACTORS = [
    {"slug": "background-removal",
     "description": "Remove backgrounds from images via inference.sh CLI, returning transparent PNGs. Uses BiRefNet model. Use when user wants to cut out image subjects or create transparent backgrounds."},
    {"slug": "elevenlabs-music",
     "description": "Generate original music from text prompts with customizable duration, genre, and mood. Uses ElevenLabs AI via inference.sh CLI."},
    {"slug": "case-study-writing",
     "description": "Structured B2B case study creation with STAR framework, metrics visualization, and research-backed narrative. Use when writing customer success stories or marketing case studies."},
    {"slug": "press-release-writing",
     "description": "Professional press releases in AP style with inverted pyramid structure. Use when announcing product launches, funding rounds, or company milestones."},
    {"slug": "landing-page-design",
     "description": "Landing page conversion optimization with layout rules, hero design, and CTA psychology. Use when designing, critiquing, or improving a landing page."},
    {"slug": "image-upscaling",
     "description": "Upscale and enhance images using Real-ESRGAN and other inference.sh models. Use when increasing image resolution or restoring image quality."},
    {"slug": "speech-to-text",
     "description": "Transcribe audio to text using ElevenLabs Scribe or Whisper models. Use when converting voice recordings, podcasts, or meetings to text."},
    {"slug": "citation-management",
     "description": "Search academic databases, extract metadata, validate citations, and generate BibTeX entries. Use when managing references for research papers."},
    {"slug": "seo-review",
     "description": "Technical SEO audit and content optimization with keyword analysis, meta tags, and structured data. Use when improving search engine rankings."},
    {"slug": "attack-tree-construction",
     "description": "Construct formal attack trees for threat modeling and security analysis. Use when identifying attack paths, modeling adversaries, or conducting red team planning."},
    {"slug": "database-schema-design",
     "description": "Design normalized relational database schemas with ER diagrams, index strategy, and migration plans. Use when creating or refactoring database structure."},
    {"slug": "prd-development",
     "description": "Product Requirements Document creation with user stories, acceptance criteria, and prioritization frameworks. Use when writing PRDs or feature specifications."},
    {"slug": "remotion-video",
     "description": "Programmatic video creation with React components, animations, and audio synchronization. Use when generating videos from code."},
    {"slug": "marketing-ideas",
     "description": "Generate targeted marketing campaign ideas, messaging frameworks, and channel strategies. Use when brainstorming marketing approaches for products or services."},
    {"slug": "finance-based-pricing-advisor",
     "description": "Financial pricing strategy with unit economics, CLV modeling, and competitive benchmarking. Use when setting or optimizing product pricing."},
    {"slug": "turborepo",
     "description": "Monorepo build system optimization with Turborepo caching, pipeline configuration, and workspace management. Use when managing large multi-package repositories."},
    {"slug": "research",
     "description": "Conduct structured research on any topic using iterative search, source evaluation, and synthesis. Use when investigating a subject and compiling findings."},
    {"slug": "notebooklm",
     "description": "Create and manage Google NotebookLM notebooks, upload sources, and generate AI-powered summaries. Use when building research notebooks from documents."},
    {"slug": "typefully",
     "description": "Draft, schedule, and optimize Twitter/X threads and posts using Typefully. Use when creating social media content with scheduling and analytics."},
]

assert len(DISTRACTORS) == 19, f"Need exactly 19 distractors, got {len(DISTRACTORS)}"


# ── Router ────────────────────────────────────────────────────────────────────

def route(query: str, library: list[dict]) -> str:
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
        'Reply with the skill slug only, or "none" if no skill applies.'
    )

    for attempt in range(3):
        try:
            r = subprocess.run(
                [OPENCLAW, "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120,
            )
            j = json.loads(r.stdout)
            return j.get("outputs", [{}])[0].get("text", "").strip().lower()
        except subprocess.TimeoutExpired:
            print(f"      [WARN] timeout attempt {attempt+1}/3")
            time.sleep(5)
        except Exception as e:
            print(f"      [ERR] {e}")
    return "none"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    queries_data  = json.loads(QUERIES.read_text())
    variants_data = json.loads(VARIANTS.read_text())

    # Build lookups
    queries_by_slug  = {s["slug"]: s["queries"]   for s in queries_data}
    variants_by_slug = {s["slug"]: s["variants"]  for s in variants_data}

    # Intersect: only skills that have BOTH queries and variants
    slugs = sorted(set(queries_by_slug) & set(variants_by_slug))
    print(f"Skills with queries + variants: {len(slugs)}")

    # Resume
    rows = []
    done_keys = set()
    if OUT.exists():
        rows = json.loads(OUT.read_text())
        done_keys = {(r["slug"], r["variant"], r["query_type"], r["query"][:40])
                     for r in rows}

    total_todo = len(slugs) * len(VARIANT_ORDER) * 10
    print(f"Total decisions: {len(slugs)} × {len(VARIANT_ORDER)} × 10 = {total_todo}")
    print(f"Already done: {len(rows)}\n")

    for slug in slugs:
        queries  = queries_by_slug[slug]
        variants = variants_by_slug[slug]

        print(f"\n{'='*60}")
        print(f"[{slug}]")

        for v_name in VARIANT_ORDER:
            v_desc = variants.get(v_name, variants["original"])

            # Build library: focal skill with this variant + 19 distractors
            library = [{"slug": slug, "description": v_desc}] + DISTRACTORS

            # All queries for this skill
            all_queries = []
            for qt in ["goal_based", "symptom_based", "context_based", "boundary"]:
                for q in queries.get(qt, []):
                    all_queries.append((qt, q))

            for qt, q in all_queries:
                key = (slug, v_name, qt, q[:40])
                if key in done_keys:
                    continue  # resume

                chosen   = route(q, library)
                triggered = slug in chosen
                # For boundary: correct = NOT triggered; for others: correct = triggered
                if qt == "boundary":
                    correct = not triggered
                else:
                    correct = triggered

                status = "✓" if correct else "✗"
                print(f"  [{v_name:<10}] {qt:<14} {status}  "
                      f"chosen={chosen:<25}  q={q[:40]}")

                rows.append({
                    "slug":        slug,
                    "variant":     v_name,
                    "query_type":  qt,
                    "query":       q,
                    "chosen":      chosen,
                    "triggered":   triggered,
                    "correct":     correct,
                })
                done_keys.add(key)
                OUT.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
                time.sleep(0.3)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("RESULTS SUMMARY")
    print("=" * 65)

    recall_types  = ["goal_based", "symptom_based", "context_based"]
    precision_type = "boundary"

    print(f"\n{'Variant':<12}  {'Recall':>8}  {'Precision':>10}  n")
    print("-" * 40)
    for v in VARIANT_ORDER:
        rec_rows = [r for r in rows if r["variant"]==v and r["query_type"] in recall_types]
        prec_rows= [r for r in rows if r["variant"]==v and r["query_type"]==precision_type]
        recall    = sum(r["correct"] for r in rec_rows)  / len(rec_rows)  * 100 if rec_rows  else 0
        precision = sum(r["correct"] for r in prec_rows) / len(prec_rows) * 100 if prec_rows else 0
        print(f"  {v:<10}  {recall:>7.1f}%  {precision:>9.1f}%  {len(rec_rows)+len(prec_rows)}")

    print(f"\n[✓] Results → {OUT}  ({len(rows)} decisions)")


if __name__ == "__main__":
    main()
