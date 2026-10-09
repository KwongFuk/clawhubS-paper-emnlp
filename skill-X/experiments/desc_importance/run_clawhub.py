#!/usr/bin/env python3
"""
ClawHub Description Importance Experiment
==========================================
20 real ClawHub skills with opaque names (nano-banana-2, p-video, etc.)
5 focal skills × 3 description variants × 4 queries = 60 routing decisions

Variants:
  original    — real ClawHub marketplace description (full)
  truncated   — first sentence only (minimal/lazy description)
  del_trigger — remove capability keywords, keep only name-level info

Metric: did agent trigger (read) the focal skill?  ✓ / ✗
Router: openclaw infer model run (same LLM call OpenClaw uses internally)
         with real <available_skills> XML — identical to run_v3.py
"""

import json, re, subprocess, time
from pathlib import Path

OPENCLAW  = "openclaw"
DATA      = Path(__file__).parent / "clawhub_10.json"
RESULTS   = Path(__file__).parent / "results_clawhub"
RESULTS.mkdir(parents=True, exist_ok=True)

# ── Load the 20 ClawHub skills ────────────────────────────────────────────────
skills_data = json.loads(DATA.read_text())

# Build library dict: slug → skill info
LIBRARY = {s["slug"]: s for s in skills_data}

# ── 5 focal skills + manually crafted implicit queries ────────────────────────
# Queries must be implicit (don't mention skill name/brand)
# 2 positive (should trigger focal) + 2 boundary (related but should NOT trigger focal)

FOCAL = {
    "nano-banana-2": {
        "positive": [
            "Create an image of a futuristic cityscape at sunset using the latest Gemini model.",
            "Edit my photo to remove the background and make it look more vibrant.",
        ],
        "boundary": [
            "Generate a 10-second video clip of waves crashing on a beach.",   # → video skill
            "Transcribe the audio from my recorded meeting into text.",          # → stt skill
        ],
    },
    "elevenlabs-music": {
        "positive": [
            "Create a 30-second upbeat electronic track for my YouTube intro.",
            "Generate background music with a calm acoustic guitar feel for my podcast.",
        ],
        "boundary": [
            "Convert my voice recording to sound like a different person.",       # → voice-changer
            "Generate sound effects for a door creaking open in my video game.", # → sound-effects
        ],
    },
    "background-removal": {
        "positive": [
            "Remove the background from this product photo and make it transparent.",
            "I need a clean PNG with no background from this portrait photo.",
        ],
        "boundary": [
            "Upscale this low-resolution image to 4K quality.",                  # → image-upscaling
            "Generate a new image of a cat sitting on a red couch.",             # → nano-banana
        ],
    },
    "case-study-writing": {
        "positive": [
            "Write a customer success story about how our SaaS reduced churn by 40%.",
            "Help me create a B2B case study using the STAR method for our enterprise client.",
        ],
        "boundary": [
            "Draft a press release announcing our product launch next Monday.",   # → press-release
            "Design a landing page for our upcoming webinar registration.",       # → landing-page
        ],
    },
    "related-skill": {
        "positive": [
            "I need to find and install a skill that helps with video generation.",
            "What skills are available for working with audio and music in this ecosystem?",
        ],
        "boundary": [
            "Search the web for the latest AI research papers on diffusion models.", # → web search
            "List all files in my current directory and summarize their contents.",   # → file ops
        ],
    },
}

# ── Description variant builder ───────────────────────────────────────────────

def make_variants(slug: str) -> dict[str, str]:
    original = LIBRARY[slug]["description"]
    sentences = re.split(r"(?<=[.!?])\s+", original.strip())

    # truncated: first sentence only — simulates a minimal/lazy description
    truncated = sentences[0] if sentences else original

    # del_trigger: keep ONLY the first sentence (just what it does, not when/how)
    # Real ClawHub descriptions: first sentence = concept, rest = triggers/features
    # This mimics IC-1 pattern: capability visible in name+first-line but triggers missing
    del_trig = truncated  # use same as truncated for ClawHub skills
    # If first sentence > 120 chars, cut at first comma or semicolon
    if len(del_trig) > 120:
        for sep in [". ", ", via", ", using", ", supporting", ". "]:
            idx = del_trig.find(sep)
            if 30 < idx < 120:
                del_trig = del_trig[:idx] + "."
                break

    return {
        "original"   : original,
        "truncated"  : truncated,
        "del_trigger": del_trig,
    }

# ── Routing call (same as run_v3.py) ─────────────────────────────────────────

def route(query: str, library_view: list[dict]) -> str:
    """Returns the skill slug the LLM would load, or 'none'.
    Uses the same openclaw infer model run call that OpenClaw's router uses.
    """
    skill_lines = []
    for s in library_view:
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
        'Reply with the skill slug only (e.g. \"background-removal\"), or \"none\" if no skill applies.'
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
    print("=" * 60)
    print("ClawHub Description Importance Experiment")
    print(f"Library: {len(LIBRARY)} real ClawHub skills (opaque names)")
    print(f"Focal:   {len(FOCAL)} skills × 3 variants × 4 queries = "
          f"{len(FOCAL)*3*4} routing decisions")
    print("=" * 60)

    all_rows = []
    baseline   = LIBRARY  # all 20 skills with original descriptions

    for focal_slug, qdef in FOCAL.items():
        variants = make_variants(focal_slug)
        print(f"\n[{focal_slug.upper()}]")
        for v_name, v_desc in variants.items():
            print(f"  variant={v_name:<14}  "
                  f"desc[:{min(90,len(v_desc))}]: {v_desc[:90]!r}")

        for v_name, v_desc in variants.items():
            # Build library with this focal skill's description swapped
            lib_view = []
            for slug, s in LIBRARY.items():
                if slug == focal_slug:
                    lib_view.append({**s, "description": v_desc})
                else:
                    lib_view.append(s)

            # Positive queries
            for q in qdef["positive"]:
                chosen = route(q, lib_view)
                triggered = focal_slug in chosen
                status = "✓" if triggered else "✗"
                print(f"  [{v_name:<14}] POS {status}  "
                      f"chosen={chosen:<30}  q={q[:50]}")
                all_rows.append({
                    "skill": focal_slug, "variant": v_name,
                    "query_type": "positive", "query": q,
                    "chosen": chosen, "triggered": triggered,
                    "should_trigger": True, "correct": triggered,
                })

            # Boundary queries
            for q in qdef["boundary"]:
                chosen = route(q, lib_view)
                triggered = focal_slug in chosen
                correct = not triggered   # correct = did NOT trigger focal
                status = "✓" if correct else "✗"
                print(f"  [{v_name:<14}] BND {status}  "
                      f"chosen={chosen:<30}  q={q[:50]}")
                all_rows.append({
                    "skill": focal_slug, "variant": v_name,
                    "query_type": "boundary", "query": q,
                    "chosen": chosen, "triggered": triggered,
                    "should_trigger": False, "correct": correct,
                })

    # ── Results ───────────────────────────────────────────────────────────────
    out = RESULTS / "results.json"
    with open(out, "w") as f:
        json.dump(all_rows, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    variants = ["original", "truncated", "del_trigger"]
    print(f"\n{'Variant':<16}  {'Recall (pos)':>13}  {'Precision (bnd)':>16}  n")
    print("-" * 55)
    for v in variants:
        pos = [r for r in all_rows if r["variant"]==v and r["query_type"]=="positive"]
        bnd = [r for r in all_rows if r["variant"]==v and r["query_type"]=="boundary"]
        rec = sum(r["correct"] for r in pos) / len(pos) * 100 if pos else 0
        pre = sum(r["correct"] for r in bnd) / len(bnd) * 100 if bnd else 0
        print(f"  {v:<14}  {rec:>12.0f}%  {pre:>15.0f}%  {len(pos)+len(bnd)}")

    print(f"\n[✓] Results → {out}")
    print(f"    Total: {len(all_rows)} routing decisions")


if __name__ == "__main__":
    main()
