#!/usr/bin/env python3
"""
ClawHub Real-Agent Description Importance Experiment
======================================================
Uses OpenClaw's REAL agent routing (not simulated).

Protocol:
  1. openclaw skills install <slug>       → install skill
  2. Patch SKILL.md description frontmatter
  3. openclaw agent --local --message Q   → real agent turn
  4. Parse output: was SKILL.md read?  → triggered: ✓/✗
  5. Restore original description

5 focal skills × 3 variants × 4 queries = 60 real agent turns
"""

import json, re, subprocess, time, shutil
from pathlib import Path

OPENCLAW = "openclaw"
WORKSPACE = Path("/Users/guangfu/codex/clawhubS")
RESULTS   = Path(__file__).parent / "results_clawhub_real"
RESULTS.mkdir(parents=True, exist_ok=True)

# ── 5 focal skills + queries ──────────────────────────────────────────────────
FOCAL = {
    "background-removal": {
        "positive": [
            "Remove the background from this product photo to get a transparent PNG.",
            "I need a clean cutout with no background from this portrait photo file.",
        ],
        "boundary": [
            "Upscale this low-resolution image to 4K quality.",
            "Generate a new image of a cat sitting on a red couch.",
        ],
    },
    "elevenlabs-music": {
        "positive": [
            "Create a 30-second upbeat electronic track for my YouTube intro.",
            "Generate calm acoustic guitar background music for my podcast episode.",
        ],
        "boundary": [
            "Change my voice recording to sound like a different person.",
            "Generate a door-creaking sound effect for my video game.",
        ],
    },
    "press-release-writing": {
        "positive": [
            "Write a press release announcing our new product launch next Monday.",
            "Draft an AP-style announcement for our company's Series B funding round.",
        ],
        "boundary": [
            "Write a B2B case study showing how our software improved client retention.",
            "Design a landing page for our upcoming webinar registration.",
        ],
    },
    "image-upscaling": {
        "positive": [
            "Upscale this low-resolution JPEG to 4x its original size.",
            "Enhance the quality of this blurry product photo using AI upscaling.",
        ],
        "boundary": [
            "Remove the background from this product shot.",
            "Generate an image of a mountain landscape at sunrise.",
        ],
    },
    "case-study-writing": {
        "positive": [
            "Help me write a B2B case study using STAR method for an enterprise client.",
            "Create a customer success story about how our SaaS reduced churn by 40%.",
        ],
        "boundary": [
            "Write an AP-style press release for our product launch.",
            "Design a high-converting landing page layout for our SaaS product.",
        ],
    },
}

# Variants: original (keep as-is), truncated (first sentence only), del_trigger (strip conditions)
def make_variants(original_desc: str) -> dict[str, str]:
    # truncated: first sentence only
    sentences = re.split(r"(?<=[.!?])\s+", original_desc.strip())
    truncated = sentences[0] if sentences else original_desc

    # del_trigger: remove "Use when", "Use for", "Trigger" clauses
    del_trig = re.sub(
        r"(Use when|Use for|Trigger when|Trigger on|When to use)[^.!?]*[.!?]",
        "", original_desc, flags=re.IGNORECASE
    ).strip()
    del_trig = re.sub(r"\s{2,}", " ", del_trig)
    # If nothing was stripped, take first 2 sentences
    if del_trig == original_desc or len(del_trig) > 0.9 * len(original_desc):
        del_trig = " ".join(sentences[:2]) if len(sentences) > 1 else truncated

    return {
        "original":    original_desc,
        "truncated":   truncated,
        "del_trigger": del_trig,
    }

# ── Skill file management ─────────────────────────────────────────────────────

def find_skill_md(slug: str) -> Path | None:
    """Find SKILL.md for an installed skill in this workspace."""
    for p in WORKSPACE.rglob("SKILL.md"):
        if p.parent.name == slug or slug in str(p):
            return p
    # Common locations
    candidates = [
        WORKSPACE / ".claude" / "skills" / slug / "SKILL.md",
        WORKSPACE / "skills" / slug / "SKILL.md",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None

def install_skill(slug: str) -> Path | None:
    """Install skill from ClawHub and return path to SKILL.md."""
    existing = find_skill_md(slug)
    if existing:
        print(f"  [already installed] {existing}")
        return existing

    print(f"  [installing] {slug}...")
    r = subprocess.run(
        [OPENCLAW, "skills", "install", slug, "--force"],
        capture_output=True, text=True, timeout=60, cwd=str(WORKSPACE)
    )
    if r.returncode != 0:
        print(f"  [WARN] install failed: {r.stderr[:200]}")
        return None

    path = find_skill_md(slug)
    if path:
        print(f"  [installed] {path}")
    else:
        print(f"  [WARN] SKILL.md not found after install")
    return path

def get_description(skill_md: Path) -> str:
    """Extract description from SKILL.md frontmatter."""
    content = skill_md.read_text()
    m = re.search(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return ""
    fm = m.group(1)
    dm = re.search(r"^description\s*:\s*(.+)$", fm, re.MULTILINE)
    if dm:
        return dm.group(1).strip().strip('"').strip("'")
    return ""

def patch_description(skill_md: Path, new_desc: str) -> str:
    """Patch description in SKILL.md frontmatter. Returns original description."""
    content = skill_md.read_text()
    original = get_description(skill_md)

    # Escape for YAML
    safe = new_desc.replace('"', '\\"')
    patched = re.sub(
        r"(^description\s*:\s*)(.+)$",
        f'\\1"{safe}"',
        content,
        flags=re.MULTILINE
    )
    if patched == content:
        # description field not found, try to add to frontmatter
        patched = content.replace("---\n", f'---\ndescription: "{safe}"\n', 1)

    skill_md.write_text(patched)
    return original

def restore_description(skill_md: Path, original: str):
    """Restore original description."""
    patch_description(skill_md, original)

# ── Real agent turn ───────────────────────────────────────────────────────────

def run_agent(query: str, slug: str) -> dict:
    """Run a real OpenClaw agent turn and detect if slug's SKILL.md was read."""
    result = {"triggered": False, "output": "", "error": ""}
    try:
        r = subprocess.run(
            [OPENCLAW, "agent", "--local", "--message", query, "--json"],
            capture_output=True, text=True, timeout=180, cwd=str(WORKSPACE)
        )
        out = r.stdout + r.stderr
        result["output"] = out[:2000]

        # Detect skill invocation: agent reads the SKILL.md file
        # Look for read calls containing the skill slug
        triggered = (
            f"/{slug}/" in out.lower() or
            f"{slug}/SKILL.md" in out.lower() or
            f'"{slug}"' in out.lower() or
            f"read({slug}" in out.lower()
        )
        result["triggered"] = triggered

    except subprocess.TimeoutExpired:
        result["error"] = "timeout"
    except Exception as e:
        result["error"] = str(e)

    return result

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("ClawHub REAL Agent Description Importance Experiment")
    print(f"{len(FOCAL)} skills × 3 variants × 4 queries = 60 agent turns")
    print("=" * 60)

    all_rows = []

    for slug, qdef in FOCAL.items():
        print(f"\n{'='*50}")
        print(f"[{slug.upper()}]")

        # Install skill
        skill_md = install_skill(slug)
        if not skill_md:
            print(f"  [SKIP] cannot install {slug}")
            continue

        # Get original description from SKILL.md
        original_desc = get_description(skill_md)
        print(f"  original desc ({len(original_desc)} chars): {original_desc[:80]}...")

        variants = make_variants(original_desc)
        for v_name, v_desc in variants.items():
            print(f"\n  -- variant: {v_name} ({len(v_desc)} chars) --")
            print(f"     desc: {v_desc[:80]}...")

        for v_name, v_desc in variants.items():
            # Patch description
            if v_name != "original":
                patch_description(skill_md, v_desc)

            # Run queries
            for q_type, queries in [("positive", qdef["positive"]),
                                     ("boundary", qdef["boundary"])]:
                for q in queries:
                    result = run_agent(q, slug)
                    triggered = result["triggered"]
                    should_trigger = (q_type == "positive")
                    correct = (triggered == should_trigger)
                    status = "✓" if correct else "✗"
                    print(f"  [{v_name:<14}] {q_type[:3].upper()} {status}  "
                          f"triggered={str(triggered):<6}  q={q[:50]}")
                    if result["error"]:
                        print(f"    ERR: {result['error']}")

                    all_rows.append({
                        "skill": slug, "variant": v_name,
                        "query_type": q_type, "query": q,
                        "triggered": triggered,
                        "should_trigger": should_trigger,
                        "correct": correct,
                        "error": result["error"],
                    })
                    time.sleep(1)

            # Restore original description after all variants done for this skill
            if v_name != "original":
                restore_description(skill_md, original_desc)

        # Final restore
        restore_description(skill_md, original_desc)

    # ── Summary ───────────────────────────────────────────────────────────────
    out = RESULTS / "results.json"
    with open(out, "w") as f:
        json.dump(all_rows, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"\n{'Variant':<16}  {'Recall':>8}  {'Precision':>10}  n")
    print("-" * 45)
    for v in ["original", "truncated", "del_trigger"]:
        pos = [r for r in all_rows if r["variant"]==v and r["query_type"]=="positive"]
        bnd = [r for r in all_rows if r["variant"]==v and r["query_type"]=="boundary"]
        rec = sum(r["correct"] for r in pos)/len(pos)*100 if pos else 0
        pre = sum(r["correct"] for r in bnd)/len(bnd)*100 if bnd else 0
        print(f"  {v:<14}  {rec:>7.0f}%  {pre:>9.0f}%  {len(pos)+len(bnd)}")

    print(f"\n→ {out}  ({len(all_rows)} agent turns)")


if __name__ == "__main__":
    main()
