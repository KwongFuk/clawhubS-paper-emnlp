#!/usr/bin/env python3
"""
IC-1 Description Gap → Routing Failure Experiment
===================================================
Uses real IC-1 skills where description has FUNCTIONAL gaps vs body.

5 focal IC-1 skills × 2 description variants × 4 queries:
  - desc_trigger  (2): matches description → should trigger with BOTH variants
  - body_trigger  (2): matches body but NOT description → fails with original,
                        succeeds with improved

Variants:
  original  — real marketplace description (short, functional gap)
  improved  — original + body-specific trigger clauses extracted from SKILL.md

Expected result:
  original  → Recall(desc_trigger)=~100%, Recall(body_trigger)=~0-30%
  improved  → Recall(desc_trigger)=~100%, Recall(body_trigger)=~80-100%
  Delta     → proves description quality causally affects routing

Router: openclaw infer model run (same LLM call as real OpenClaw routing)
"""

import json, re, subprocess, time
from pathlib import Path

OPENCLAW = "openclaw"
RESULTS  = Path(__file__).parent / "results_ic1"
RESULTS.mkdir(parents=True, exist_ok=True)

# ── IC-1 focal skills with hand-crafted description improvements ──────────────
# Each skill: original desc (from marketplace) vs improved desc (adds body triggers)
# Queries: 2 desc_trigger (description covers it) + 2 body_trigger (body covers it, desc doesn't)

FOCAL = {
    "task-runner": {
        "original": (
            "Manage and track tasks and projects persistently with priorities, "
            "completion status, filtering, and secure markdown export across sessions."
        ),
        "improved": (
            "Manage and track tasks and projects persistently with priorities, "
            "completion status, filtering, and secure markdown export across sessions. "
            "Also use when running project-specific build recipes with `just` (a modern "
            "command runner), listing available justfile recipes, executing test/build/lint "
            "commands, or running any justfile target from the command line."
        ),
        "desc_trigger": [
            "Add a high-priority task called 'Fix login bug' to my project tracker.",
            "Show me all incomplete tasks filtered by the 'backend' tag.",
        ],
        "body_trigger": [
            "List all available recipes in my justfile and tell me how to run them.",
            "Run the test recipe from my justfile and show the output.",
        ],
    },
    "guardian": {
        "original": (
            "One layer in a multi-layer security stack for OpenClaw agents. "
            "Intercepts prompt injection, exfiltration attempts, tool abuse, "
            "and social engineering before they reach the agent."
        ),
        "improved": (
            "One layer in a multi-layer security stack for OpenClaw agents. "
            "Intercepts prompt injection, exfiltration attempts, tool abuse, "
            "and social engineering. Also use when you need to classify git changes, "
            "optimize commit or PR structure, score code quality and risk, "
            "detect security-sensitive diffs, prepare reviewer guidance, "
            "or generate release notes from a branch."
        ),
        "desc_trigger": [
            "Block any prompt injection attempt in this incoming agent message.",
            "Check this tool call for signs of exfiltration or social engineering.",
        ],
        "body_trigger": [
            "Classify my git diff and tell me which changes are security-sensitive.",
            "Score the quality and risk of this PR and prepare reviewer guidance.",
        ],
    },
    "pr-ship": {
        "original": (
            "Pre-ship risk report for OpenClaw PRs. Dynamically explores the codebase "
            "to assess module risk, blast radius, and version-specific gotchas. "
            "Scores each finding by severity."
        ),
        "improved": (
            "Pre-ship risk report for OpenClaw PRs. Dynamically explores the codebase "
            "to assess module risk, blast radius, and version-specific gotchas. "
            "Also use when writing PR descriptions, improving commit hygiene, "
            "choosing a merge strategy, or verifying post-merge steps. "
            "Teaches PR communication best practices and how to structure commits clearly."
        ),
        "desc_trigger": [
            "Generate a risk report for my PR before I merge it to main.",
            "Assess the blast radius of the changes in this pull request.",
        ],
        "body_trigger": [
            "Help me write a clear, well-structured pull request description for my changes.",
            "How should I format my commit messages and choose a merge strategy for this PR?",
        ],
    },
    "openclaw-watchdog": {
        "original": (
            "Self-healing monitoring system for OpenClaw gateway. "
            "Auto-detects failures, fixes crashes, and sends Telegram alerts."
        ),
        "improved": (
            "Self-healing monitoring system for OpenClaw gateway. "
            "Auto-detects failures, fixes crashes, and sends Telegram alerts. "
            "Use when setting up gateway monitoring, watchdog services, auto-recovery, "
            "health checks, log analysis, or diagnosing gateway crash loops. "
            "Requires Telegram bot token for alert delivery."
        ),
        "desc_trigger": [
            "Set up auto-recovery for my OpenClaw gateway when it crashes.",
            "Send me a Telegram alert whenever the OpenClaw gateway goes down.",
        ],
        "body_trigger": [
            "Set up a watchdog service that checks gateway health and restarts it on failure.",
            "Analyze the gateway crash logs to diagnose why it keeps restarting.",
        ],
    },
    "near-intents": {
        "original": (
            "Universal cross-chain swap & bridge skill for OpenClaw using the NEAR "
            "Intents 1Click SDK. Supports 14+ blockchains including NEAR, Base, "
            "Ethereum, Solana."
        ),
        "improved": (
            "Universal cross-chain swap & bridge skill for OpenClaw using the NEAR "
            "Intents 1Click SDK. Supports 14+ blockchains. Use when getting a swap "
            "quote via the 1Click REST API, building deposit transactions, submitting "
            "cross-chain deposits, checking swap status, or integrating the swap widget "
            "into a React app or Node.js script."
        ),
        "desc_trigger": [
            "Swap 1 ETH on Ethereum to NEAR tokens using the cross-chain bridge.",
            "Bridge my USDC from Base to Solana using NEAR Intents.",
        ],
        "body_trigger": [
            "Get a swap quote from the 1Click REST API for 100 USDC from Base to NEAR.",
            "Build a Node.js script that submits a cross-chain deposit and polls for status.",
        ],
    },
}

# ── Library: 15 distractor skills (real ClawHub, diverse domains) ─────────────
# These compete with focal skills during routing
DISTRACTORS = [
    {
        "slug": "background-removal",
        "description": "Remove backgrounds from images via inference.sh CLI, returning transparent PNGs. Uses BiRefNet model for high accuracy. Triggers: remove background, transparent PNG, cut out image.",
    },
    {
        "slug": "elevenlabs-music",
        "description": "Generate original music from text prompts with customizable duration, genre, and mood. Uses ElevenLabs AI via inference.sh CLI.",
    },
    {
        "slug": "case-study-writing",
        "description": "Structured B2B case study creation with STAR framework, metrics visualization, and research-backed narrative. Use when writing customer success stories.",
    },
    {
        "slug": "press-release-writing",
        "description": "Professional press releases in AP style with inverted pyramid structure. Use when announcing product launches, funding rounds, or company news.",
    },
    {
        "slug": "landing-page-design",
        "description": "Landing page conversion optimization with layout rules, hero design, and CTA psychology. Use when designing or critiquing a landing page.",
    },
    {
        "slug": "image-upscaling",
        "description": "Upscale and enhance images using Topaz, Real-ESRGAN, and other inference.sh models. Use when increasing image resolution or quality.",
    },
    {
        "slug": "speech-to-text",
        "description": "Transcribe audio to text using ElevenLabs Scribe or Whisper models. Use when converting recordings, podcasts, or meetings to text.",
    },
    {
        "slug": "google-veo",
        "description": "Text-to-video generation using Google Veo models via inference.sh CLI. Use when generating videos from text prompts.",
    },
    {
        "slug": "markdown-token-optimizer",
        "description": "Analyze markdown files and suggest token-reduction optimizations while preserving meaning. Use when reducing LLM context costs.",
    },
    {
        "slug": "vercel-react-best-practices",
        "description": "React and Next.js performance optimization guide with 64 prioritized rules across 8 categories. Use during component writing, code review, or refactoring.",
    },
    {
        "slug": "remotion-render",
        "description": "Convert React/Remotion component code directly to MP4 videos via CLI. Use when rendering programmatic video from React components.",
    },
    {
        "slug": "related-skill",
        "description": "Discover and install complementary skills from the inference.sh registry. Use when finding what skills are available for a task domain.",
    },
    {
        "slug": "elevenlabs-voice-changer",
        "description": "Transform any voice into a different voice while preserving speech content. Use when changing speaker identity in audio recordings.",
    },
    {
        "slug": "elevenlabs-sound-effects",
        "description": "Generate royalty-free sound effects from text descriptions using ElevenLabs AI. Use for game audio, video production, or creative projects.",
    },
    {
        "slug": "ai-video-generation",
        "description": "Generate videos with 40+ AI models including Veo, Seedance, Wan, and Grok via inference.sh. Use when creating AI-generated video content.",
    },
]

# ── Routing (identical to run_v3.py) ─────────────────────────────────────────

def route(query: str, library: list[dict]) -> str:
    skill_lines = []
    for s in library:
        slug = s["slug"]
        desc = s["description"]
        path = f"/skills/{slug}/SKILL.md"
        skill_lines.append("  <skill>")
        skill_lines.append(f"    <name>{slug}</name>")
        skill_lines.append(f"    <description>{desc}</description>")
        skill_lines.append(f"    <location>{path}</location>")
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

    for attempt in range(3):
        try:
            r = subprocess.run(
                [OPENCLAW, "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120,
            )
            j = json.loads(r.stdout)
            return j.get("outputs", [{}])[0].get("text", "").strip().lower()
        except subprocess.TimeoutExpired:
            print(f"  [WARN] timeout attempt {attempt+1}/3, retrying...")
            time.sleep(5)
        except Exception as e:
            print(f"  [ERR] {e}")
    return "none"

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("IC-1 Description Gap → Routing Failure Experiment")
    print(f"5 focal IC-1 skills × 2 variants × 4 queries = 40 decisions")
    print(f"Library: 5 focal + {len(DISTRACTORS)} distractors = {5+len(DISTRACTORS)} skills total")
    print("=" * 65)

    all_rows = []

    for slug, cfg in FOCAL.items():
        print(f"\n{'='*60}")
        print(f"[{slug.upper()}]")
        orig_len = len(cfg["original"])
        impr_len = len(cfg["improved"])
        delta = impr_len - orig_len
        print(f"  original  ({orig_len:4d} chars): {cfg['original'][:80]}...")
        print(f"  improved  ({impr_len:4d} chars): +{delta} chars added")

        for variant in ["original", "improved"]:
            v_desc = cfg[variant]

            # Build library: focal skill with this variant's description + all distractors
            library = [{"slug": slug, "description": v_desc}] + DISTRACTORS

            for q_type, q_list in [("desc_trigger", cfg["desc_trigger"]),
                                    ("body_trigger", cfg["body_trigger"])]:
                for q in q_list:
                    chosen = route(q, library)
                    triggered = slug in chosen
                    correct = triggered   # all queries should trigger focal skill
                    status = "✓" if correct else "✗"
                    print(f"  [{variant:<10}] {q_type:<13} {status}  "
                          f"chosen={chosen:<25}  q={q[:45]}")
                    all_rows.append({
                        "skill": slug, "variant": variant,
                        "query_type": q_type, "query": q,
                        "chosen": chosen,
                        "triggered": triggered,
                        "correct": correct,
                    })
                    time.sleep(0.5)

    # ── Summary ───────────────────────────────────────────────────────────────
    out = RESULTS / "results.json"
    with open(out, "w") as f:
        json.dump(all_rows, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 65)
    print("RESULTS")
    print("=" * 65)

    print(f"\n{'Variant':<12}  {'Query type':<15}  {'Recall':>8}  n")
    print("-" * 45)
    for variant in ["original", "improved"]:
        for q_type in ["desc_trigger", "body_trigger"]:
            rows = [r for r in all_rows
                    if r["variant"]==variant and r["query_type"]==q_type]
            rec = sum(r["correct"] for r in rows) / len(rows) * 100 if rows else 0
            print(f"  {variant:<10}  {q_type:<15}  {rec:>7.0f}%  {len(rows)}")

    print()
    # Key metric: improvement in body_trigger recall
    orig_bt = [r for r in all_rows if r["variant"]=="original" and r["query_type"]=="body_trigger"]
    impr_bt = [r for r in all_rows if r["variant"]=="improved" and r["query_type"]=="body_trigger"]
    orig_rec = sum(r["correct"] for r in orig_bt)/len(orig_bt)*100 if orig_bt else 0
    impr_rec = sum(r["correct"] for r in impr_bt)/len(impr_bt)*100 if impr_bt else 0
    print(f"  KEY: body_trigger recall:  original={orig_rec:.0f}% → improved={impr_rec:.0f}%  "
          f"(Δ={impr_rec-orig_rec:+.0f}pp)")

    print(f"\n→ {out}  ({len(all_rows)} routing decisions)")


if __name__ == "__main__":
    main()
