#!/usr/bin/env python3
"""
Description Importance Experiment v3
=====================================
5 focal skills × 4 description variants × 4 queries = 80 routing decisions
Each decision: did agent trigger (read) the focal skill?  ✓ / ✗

Variants:
  original        — real marketplace description (baseline)
  add             — original + extra capabilities from SKILL.md not in description
  delete_trigger  — remove "Use when…" trigger conditions
  delete_boundary — remove "NOT for…" boundary constraints

Queries (per skill):
  2 positive  (should trigger)
  2 boundary  (should NOT trigger — skill is adjacent but wrong tool)

Router: openclaw infer model run → real <available_skills> XML → binary triggered/not
"""

import json, re, subprocess, sys, time
from pathlib import Path

OPENCLAW   = "openclaw"
RESULTS    = Path(__file__).parent / "results_v3"
RESULTS.mkdir(parents=True, exist_ok=True)

# ── Manually crafted skill definitions ───────────────────────────────────────
# add_suffix  : extra sentences to append for "add" variant
# del_trigger : regex hint for what "trigger" text to strip; None = truncate
# del_boundary: regex hint for what "boundary" text to strip; None = no-op
# queries     : 2 positive + 2 boundary

SKILL_DEFS = {
    "weather": {
        "add_suffix": (
            " Also applicable when user asks whether outdoor activities are safe, "
            "what to wear outside, or needs real-time wind/humidity conditions."
        ),
        "positive": [
            "Should I bring an umbrella when I head out today?",
            "What will the temperature be in Paris tomorrow morning?",
        ],
        "boundary": [
            "Pull the historical monthly rainfall records for London in 2020.",
            "Set up a push alert that texts me whenever a tornado warning is issued.",
        ],
    },
    "github": {
        "add_suffix": (
            " Also use when user wants to merge a pull request, add a reviewer, "
            "check review comments, or search for code within a repository."
        ),
        "positive": [
            "Did the CI tests pass on my latest pull request?",
            "List all open issues labeled 'bug' in the myapp repository.",
        ],
        "boundary": [
            "Open the GitHub PR page in a browser and click the Approve button.",
            "Bump the package.json version across all 80 repos in our org at once.",
        ],
    },
    "healthcheck": {
        "add_suffix": (
            " Also use when user wants to verify open ports, check pending OS security "
            "patches, review kernel version, or get a risk score for production readiness."
        ),
        "positive": [
            "Are there any exposed ports or security vulnerabilities on my VPS?",
            "Harden SSH on this machine and check for pending system updates.",
        ],
        "boundary": [
            "Monitor my website uptime and alert me if it goes down.",
            "Set up GitHub Actions to run security scans on every pull request.",
        ],
    },
    "git-helper": {
        "add_suffix": (
            " Use when user wants to check git status, commit changes, create or switch "
            "branches, pull from remote, or view commit history. "
            "Trigger on any git workflow operation."
        ),
        "positive": [
            "Show me any uncommitted changes in my working directory.",
            "Create a branch called feature/login and push it to origin.",
        ],
        "boundary": [
            "Open a pull request for my current branch on GitHub.",
            "Set up a pre-commit hook that runs linting before every commit.",
        ],
    },
    "bear-notes": {
        "add_suffix": (
            " Use when user wants to create, search, edit, delete, or tag notes in "
            "the Bear app on macOS. Trigger on any Bear note management request."
        ),
        "positive": [
            "Add a new note with today's meeting summary and tag it #work.",
            "Find the note I wrote last week about the project timeline.",
        ],
        "boundary": [
            "Save this as a reminder to call my dentist on Friday.",
            "Create a new Notion page with the project brief.",
        ],
    },
}

# ── Helper: fetch all eligible skills from openclaw ──────────────────────────

def fetch_library() -> list[dict]:
    r = subprocess.run([OPENCLAW, "skills", "list", "--json"],
                       capture_output=True, text=True, timeout=30)
    data = json.loads(r.stdout)
    eligible = [s for s in data.get("skills", []) if s.get("eligible")]

    library = []
    for s in eligible:
        info = subprocess.run([OPENCLAW, "skills", "info", s["name"], "--json"],
                              capture_output=True, text=True, timeout=15)
        try:
            d = json.loads(info.stdout)
        except Exception:
            d = {}
        library.append({
            "name"       : s["name"],
            "description": s["description"],
            "file_path"  : d.get("filePath", f"/skills/{s['name']}/SKILL.md"),
        })
    return library

# ── Description variant builders ─────────────────────────────────────────────

def make_variants(orig: str, skill_name: str) -> dict[str, str]:
    defn = SKILL_DEFS[skill_name]

    # add: original + extra capability sentences
    add_desc = orig.rstrip() + defn["add_suffix"]

    # delete_trigger: strip "Use when…" clause(s)
    del_trig = re.sub(
        r"[Uu]se when[^.!?]*[.!?]", "", orig
    ).strip()
    del_trig = re.sub(r"\s{2,}", " ", del_trig)
    if not del_trig or del_trig == orig:
        # fallback: keep only first sentence
        parts = re.split(r"(?<=[.!?])\s+", orig)
        del_trig = parts[0] if parts else orig

    # delete_boundary: strip "NOT for…" / "not for…" clause(s)
    del_bound = re.sub(
        r"NOT for[^.!?]*[.!?]", "", orig, flags=re.IGNORECASE
    ).strip()
    del_bound = re.sub(r"\s{2,}", " ", del_bound)
    if del_bound == orig:
        # try "Do not use…" pattern
        del_bound = re.sub(r"[Dd]o not use[^.!?]*[.!?]", "", orig).strip()

    return {
        "original"        : orig,
        "add"             : add_desc,
        "delete_trigger"  : del_trig,
        "delete_boundary" : del_bound,
    }

# ── Routing call ─────────────────────────────────────────────────────────────

def route(query: str, library: list[dict]) -> str:
    """Return the skill name the LLM would read, or 'none'."""
    skill_lines = []
    for s in library:
        skill_lines.append("  <skill>")
        skill_lines.append(f"    <name>{s['name']}</name>")
        skill_lines.append(f"    <description>{s['description']}</description>")
        skill_lines.append(f"    <location>{s['file_path']}</location>")
        skill_lines.append("  </skill>")

    prompt = f"""\
The following skills provide specialized instructions for specific tasks.
Use the read tool to load a skill's file when the task matches its description.

<available_skills>
{chr(10).join(skill_lines)}
</available_skills>

User request: {query}

Which single skill would you load with the read tool?
Reply with the skill name only (e.g. "weather"), or "none" if no skill applies.\
"""

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
    return "none"

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("Description Importance Experiment v3")
    print("5 skills × 4 variants × 4 queries = 80 decisions")
    print("=" * 55)

    print("\n[1] Fetching skill library from openclaw...")
    library = fetch_library()
    print(f"    {len(library)} eligible skills loaded")

    focal_skill_names = list(SKILL_DEFS.keys())

    # Build description variants for each focal skill
    orig_descs = {s["name"]: s["description"] for s in library}
    rows = []

    for focal_name in focal_skill_names:
        orig = orig_descs.get(focal_name, "")
        if not orig:
            print(f"  [SKIP] {focal_name} — not in eligible library")
            continue

        variants = make_variants(orig, focal_name)

        print(f"\n[{focal_name.upper()}]")
        for v_name, v_desc in variants.items():
            print(f"  variant={v_name:<16}  desc[:80]: {v_desc[:80]!r}")

        for v_name, v_desc in variants.items():
            # Build library with this focal skill's description swapped
            lib_view = []
            for s in library:
                if s["name"] == focal_name:
                    lib_view.append({**s, "description": v_desc})
                else:
                    lib_view.append(s)

            # Positive queries (should trigger focal skill)
            for q in SKILL_DEFS[focal_name]["positive"]:
                chosen = route(q, lib_view)
                triggered = focal_name.lower() in chosen or chosen == focal_name.split("-")[0]
                status = "✓" if triggered else "✗"
                print(f"  [{v_name:<16}] POS {status}  chosen={chosen:<20}  q={q[:55]}")
                rows.append({
                    "skill": focal_name, "variant": v_name,
                    "query_type": "positive", "query": q,
                    "chosen": chosen, "triggered": triggered,
                    "should_trigger": True,
                    "correct": triggered,
                })

            # Boundary queries (should NOT trigger focal skill)
            for q in SKILL_DEFS[focal_name]["boundary"]:
                chosen = route(q, lib_view)
                triggered = focal_name.lower() in chosen or chosen == focal_name.split("-")[0]
                # correct = focal skill NOT triggered
                correct = not triggered
                status = "✓" if correct else "✗"
                print(f"  [{v_name:<16}] BND {status}  chosen={chosen:<20}  q={q[:55]}")
                rows.append({
                    "skill": focal_name, "variant": v_name,
                    "query_type": "boundary", "query": q,
                    "chosen": chosen, "triggered": triggered,
                    "should_trigger": False,
                    "correct": correct,
                })

    # ── Results ───────────────────────────────────────────────────────────────
    out = RESULTS / "results.json"
    with open(out, "w") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 55)
    print("RESULTS")
    print("=" * 55)

    variants = ["original", "add", "delete_trigger", "delete_boundary"]
    print(f"\n{'Variant':<18}  {'Recall (pos)':>13}  {'Precision (bnd)':>16}  {'n'}")
    print("-" * 60)
    for v in variants:
        pos_rows = [r for r in rows if r["variant"]==v and r["query_type"]=="positive"]
        bnd_rows = [r for r in rows if r["variant"]==v and r["query_type"]=="boundary"]
        recall    = sum(r["correct"] for r in pos_rows) / len(pos_rows) * 100 if pos_rows else 0
        precision = sum(r["correct"] for r in bnd_rows) / len(bnd_rows) * 100 if bnd_rows else 0
        print(f"  {v:<16}  {recall:>12.0f}%  {precision:>15.0f}%  {len(pos_rows)+len(bnd_rows)}")

    print(f"\n[✓] Raw results → {out}")
    print(f"    Total decisions: {len(rows)}")


if __name__ == "__main__":
    main()
