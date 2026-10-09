#!/usr/bin/env python3
"""
skill_bias_probe.py
====================
Quick experiment: does the OpenClaw-style LLM router show
position bias, length bias, or name-similarity bias
when selecting between two comparable skills?

Method:
  Replicate the EXACT openclaw formatSkillsForPrompt() system prompt,
  then call GPT-4o-mini and measure which skill's read() is chosen.

Conditions tested:
  C1  Position bias      — identical skills, order swapped (A first vs B first)
  C2  Length bias        — same name+topic, long desc vs short desc
  C3  Name collision     — similar names, identical descriptions
  C4  Desc collision     — different names, near-identical descriptions
  C5  Name-desc conflict — name implies X, desc implies Y (A: name=X/desc=Y, B: name=Y/desc=X)

Per condition: 10 queries × 3 runs  = 30 trials
Total API calls: ~5 conditions × 10 queries × 3 runs = 150 calls (gpt-4o-mini, cheap)
"""

import os, json, re, random, time, hashlib
from pathlib import Path
from collections import Counter, defaultdict
import openai

# ─── Config ───────────────────────────────────────────────────────────────────
MODEL    = "gpt-4o-mini"
N_RUNS   = 3      # repetitions per query per condition (LLM is non-deterministic)
N_QUERY  = 10     # queries per condition
TEMP     = 0.0    # deterministic for most runs; we vary seed via system msg

client = openai.OpenAI()   # uses OPENAI_API_KEY from env

OUT_DIR  = Path(__file__).parent / "results_bias_probe"
OUT_DIR.mkdir(exist_ok=True)
RESULT_FILE = OUT_DIR / "bias_results.jsonl"

# ─── OpenClaw system prompt template (from source: formatSkillsForPrompt) ─────
OPENCLAW_SYSTEM = """\
You are a helpful AI assistant with access to tools.
You MUST respond by issuing exactly one tool call: read(path) for the skill
whose SKILL.md you want to load before answering the user.
Do NOT answer directly. Only call read() with the file path.\
"""

def build_skills_prompt(skills: list[dict]) -> str:
    """
    Replicate formatSkillsForPrompt() from:
    openclaw/dist/skills-Djbt9HGh.js
    """
    lines = [
        "\n\nThe following skills provide specialized instructions for specific tasks.",
        "Use the read tool to load a skill's file when the task matches its description.",
        "When a skill file references a relative path, resolve it against the skill directory.",
        "",
        "<available_skills>",
    ]
    for s in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{s['name']}</name>")
        lines.append(f"    <description>{s['description']}</description>")
        lines.append(f"    <location>~/.skills/{s['name']}/SKILL.md</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


# ─── Tool spec for read() ─────────────────────────────────────────────────────
READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read",
        "description": "Read a skill file from disk",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or ~ path to read"}
            },
            "required": ["path"],
        },
    },
}


def call_router(skills: list[dict], query: str, run_id: int) -> dict:
    """
    Call GPT-4o-mini with the OpenClaw skill prompt,
    return which skill was selected (by path substring matching).
    """
    skills_prompt = build_skills_prompt(skills)
    system_msg = OPENCLAW_SYSTEM + skills_prompt
    # Add run_id to system to vary seed deterministically
    system_msg += f"\n\n<!-- run={run_id} -->"

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=TEMP,
            tools=[READ_TOOL],
            tool_choice="required",
            messages=[
                {"role": "system",  "content": system_msg},
                {"role": "user",    "content": query},
            ],
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            path = msg.tool_calls[0].function.arguments
            try: path = json.loads(path).get("path", "")
            except: path = path
        else:
            path = ""

        # Map path → skill name
        selected = None
        for s in skills:
            if s["name"] in path:
                selected = s["name"]
                break

        return {
            "selected": selected,
            "raw_path": path,
            "finish": resp.choices[0].finish_reason,
        }
    except Exception as e:
        return {"selected": "ERROR", "raw_path": str(e), "finish": "error"}


def run_condition(
    condition_id: str,
    condition_name: str,
    skills_A: list[dict],   # order A (skill_a first)
    skills_B: list[dict],   # order B (skill_b first, for position test)
    queries: list[str],
    target_skill: str | None = None,  # for binary win/loss tracking
) -> dict:
    """
    Run one experimental condition.
    skills_A / skills_B are the two orderings to test.
    Returns summary statistics.
    """
    print(f"\n{'='*60}")
    print(f"CONDITION: {condition_id} — {condition_name}")
    print(f"Skills: {[s['name'] for s in skills_A]}")
    print(f"Queries: {len(queries)} × {N_RUNS} runs")

    all_records = []

    for order_label, skills_list in [("A_first", skills_A), ("B_first", skills_B)]:
        for q_idx, query in enumerate(queries):
            for run in range(N_RUNS):
                rec = call_router(skills_list, query, run_id=run)
                rec.update({
                    "condition_id":   condition_id,
                    "condition_name": condition_name,
                    "order":          order_label,
                    "query_idx":      q_idx,
                    "query":          query,
                    "run":            run,
                    "skills_order":   [s["name"] for s in skills_list],
                })
                all_records.append(rec)
                # Write incrementally
                with open(RESULT_FILE, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                print(f"  [{order_label}] q{q_idx} r{run} → {rec['selected']}")
                time.sleep(0.05)  # rate limit courtesy

    # Compute stats
    counts = Counter(r["selected"] for r in all_records)
    position_split = defaultdict(Counter)
    for r in all_records:
        position_split[r["order"]][r["selected"]] += 1

    summary = {
        "condition_id":   condition_id,
        "condition_name": condition_name,
        "total_trials":   len(all_records),
        "overall_counts": dict(counts),
        "by_order":       {k: dict(v) for k, v in position_split.items()},
    }
    print(f"  SUMMARY: {dict(counts)}")
    return summary


# ─── Condition Definitions ────────────────────────────────────────────────────

SHORT_DESC = "A weather skill that shows current temperature and forecasts."
LONG_DESC  = (
    "A comprehensive weather assistant that provides real-time temperature, "
    "hourly and weekly forecasts, precipitation probability, UV index, "
    "wind speed and direction, humidity, air quality index, and sunrise/sunset "
    "times for any city worldwide. Supports metric and imperial units, "
    "handles timezone conversions, and can schedule weather digests."
)

SKILL_A_WEATHER = {"name": "weather-assistant", "description": SHORT_DESC}
SKILL_B_WEATHER = {"name": "weather-helper",    "description": SHORT_DESC}   # similar name

SKILL_LONG  = {"name": "weather-assistant", "description": LONG_DESC}
SKILL_SHORT = {"name": "weather-helper",    "description": SHORT_DESC}

SKILL_COLLAB_A = {"name": "task-runner",
                  "description": "Runs background tasks, schedules jobs, and monitors execution status."}
SKILL_COLLAB_B = {"name": "job-scheduler",
                  "description": "Runs background tasks, schedules jobs, and monitors execution status."}

# Desc collision: near-identical descriptions, different names
NEAR_IDENTICAL_A = {
    "name": "github-manager",
    "description": "Manage GitHub repositories, issues, and pull requests via the GitHub API."
}
NEAR_IDENTICAL_B = {
    "name": "git-assistant",
    "description": "Manage GitHub repositories, issues, and pull requests through the GitHub API."
}

# Name-desc conflict: name says calendar, desc says weather (and vice versa)
CONFLICT_A = {
    "name": "calendar-assistant",
    "description": "Get current weather forecasts and temperature for any city."
}
CONFLICT_B = {
    "name": "weather-assistant",
    "description": "Manage calendar events, schedule meetings, and set reminders."
}

# A decoy skill always present (unrelated, to avoid forcing binary)
DECOY = {
    "name": "code-formatter",
    "description": "Format and lint Python, JavaScript, and TypeScript code files."
}

# Queries for each condition
WEATHER_QUERIES = [
    "What is the weather like in Tokyo today?",
    "Show me the forecast for this week",
    "Will it rain tomorrow in New York?",
    "What's the temperature right now?",
    "Give me a weather update for London",
    "Is it sunny outside?",
    "Current humidity level please",
    "Weather forecast for the weekend",
    "How hot is it in Sydney?",
    "Tell me today's weather",
]

TASK_QUERIES = [
    "Schedule a background job to run every hour",
    "Run this script in the background and monitor it",
    "Queue a task for later execution",
    "Check the status of my scheduled jobs",
    "Set up a cron-like schedule for data processing",
    "Monitor this long-running process",
    "Create a recurring task",
    "Execute this command in the background",
    "View all running jobs",
    "Cancel a scheduled task",
]

GITHUB_QUERIES = [
    "Create a new GitHub issue",
    "List open pull requests in my repo",
    "Merge this PR",
    "Show me recent commits",
    "Close the issue #42",
    "Add a label to this pull request",
    "Get the status of CI checks",
    "Star a repository",
    "Fork this repo",
    "Review pull request comments",
]

WEATHER_QUERIES_2 = WEATHER_QUERIES  # same queries for conflict condition


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    summaries = []

    # C1: Position bias — same skills, swap order, same queries
    print("\n[C1] POSITION BIAS: same two skills, swap order")
    s = run_condition(
        "C1_position",
        "Position bias (same skills, swapped order)",
        skills_A=[SKILL_A_WEATHER, SKILL_B_WEATHER, DECOY],
        skills_B=[SKILL_B_WEATHER, SKILL_A_WEATHER, DECOY],
        queries=WEATHER_QUERIES,
    )
    summaries.append(s)

    # C2: Length bias — same topic+name, one long desc, one short
    print("\n[C2] LENGTH BIAS: same name, long vs short description")
    s = run_condition(
        "C2_length",
        "Length bias (long vs short description)",
        skills_A=[SKILL_LONG,  SKILL_SHORT, DECOY],
        skills_B=[SKILL_SHORT, SKILL_LONG,  DECOY],
        queries=WEATHER_QUERIES,
    )
    summaries.append(s)

    # C3: Name similarity — same descriptions, similar (not identical) names
    print("\n[C3] NAME COLLISION: similar names, identical descriptions")
    s = run_condition(
        "C3_name_collision",
        "Name collision (similar names, same description)",
        skills_A=[SKILL_A_WEATHER, SKILL_B_WEATHER, DECOY],
        skills_B=[SKILL_B_WEATHER, SKILL_A_WEATHER, DECOY],
        queries=WEATHER_QUERIES,
    )
    summaries.append(s)

    # C4: Description collision — different names, near-identical descriptions
    print("\n[C4] DESC COLLISION: different names, near-identical descriptions")
    s = run_condition(
        "C4_desc_collision",
        "Description collision (near-identical descs, different names)",
        skills_A=[NEAR_IDENTICAL_A, NEAR_IDENTICAL_B, DECOY],
        skills_B=[NEAR_IDENTICAL_B, NEAR_IDENTICAL_A, DECOY],
        queries=GITHUB_QUERIES,
    )
    summaries.append(s)

    # C5: Name-desc conflict — name says X, desc says Y
    print("\n[C5] NAME-DESC CONFLICT: name and description disagree")
    s = run_condition(
        "C5_conflict",
        "Name-description conflict (name≠desc)",
        skills_A=[CONFLICT_A, CONFLICT_B, DECOY],
        skills_B=[CONFLICT_B, CONFLICT_A, DECOY],
        queries=WEATHER_QUERIES_2,
    )
    summaries.append(s)

    # ─── Print report ─────────────────────────────────────────────────────────
    print("\n" + "="*70)
    print("BIAS PROBE — FINAL REPORT")
    print("="*70)
    for s in summaries:
        print(f"\n[{s['condition_id']}] {s['condition_name']}")
        print(f"  Overall selection: {s['overall_counts']}")
        for order, counts in s['by_order'].items():
            print(f"  Order={order}: {counts}")

    # Save summaries
    out = OUT_DIR / "bias_summary.json"
    out.write_text(json.dumps(summaries, indent=2, ensure_ascii=False))
    print(f"\nFull results: {RESULT_FILE}")
    print(f"Summary:      {out}")

    # ─── Bias analysis ────────────────────────────────────────────────────────
    print("\n" + "="*70)
    print("BIAS ANALYSIS")
    print("="*70)

    for s in summaries:
        cid = s['condition_id']
        by_order = s['by_order']
        total = s['total_trials']

        if cid == "C1_position":
            # Do skills in position-0 win more?
            a_in_a = by_order.get("A_first", {})
            b_in_b = by_order.get("B_first", {})
            pos0_wins = a_in_a.get("weather-assistant", 0) + b_in_b.get("weather-helper", 0)
            pos0_total = sum(a_in_a.values()) + sum(b_in_b.values())
            pct = pos0_wins / pos0_total * 100 if pos0_total else 0
            print(f"\nC1 Position bias: position-0 skill wins {pct:.1f}% of trials")
            if pct > 60:   print("  → STRONG position bias (>60%)")
            elif pct > 50: print("  → Mild position bias")
            else:          print("  → No position bias")

        elif cid == "C2_length":
            long_wins = sum(
                v.get("weather-assistant", 0)
                for v in by_order.values()
            )
            total_v = sum(sum(v.values()) for v in by_order.values())
            pct = long_wins / total_v * 100 if total_v else 0
            print(f"\nC2 Length bias: long-desc skill wins {pct:.1f}% of trials")
            if pct > 60:   print("  → STRONG length bias (longer desc preferred)")
            elif pct < 40: print("  → STRONG reverse length bias (shorter preferred)")
            else:           print("  → No significant length bias")

        elif cid == "C5_conflict":
            # Which wins: name-match or desc-match?
            # CONFLICT_A: name=calendar-assistant, desc=weather → desc wins if weather query calls it
            # CONFLICT_B: name=weather-assistant, desc=calendar → name wins if weather query calls it
            weather_name_wins = sum(
                v.get("weather-assistant", 0) for v in by_order.values()
            )
            weather_desc_wins = sum(
                v.get("calendar-assistant", 0) for v in by_order.values()
            )
            total_v = weather_name_wins + weather_desc_wins
            if total_v:
                print(f"\nC5 Name vs Desc priority:")
                print(f"  Name match wins (weather-assistant via its name): {weather_name_wins} ({weather_name_wins/total_v*100:.1f}%)")
                print(f"  Desc match wins (calendar-assistant via its desc): {weather_desc_wins} ({weather_desc_wins/total_v*100:.1f}%)")
                if weather_name_wins > weather_desc_wins * 1.5:
                    print("  → NAME dominates routing (name > description)")
                elif weather_desc_wins > weather_name_wins * 1.5:
                    print("  → DESCRIPTION dominates routing")
                else:
                    print("  → Mixed: name and description roughly co-equal")


if __name__ == "__main__":
    main()
