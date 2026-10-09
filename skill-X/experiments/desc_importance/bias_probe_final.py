#!/usr/bin/env python3
"""
bias_probe_final.py
===================
用 openclaw infer model run 模拟 OpenClaw skill routing，
测试位置偏差、长度偏差、描述碰撞、名字碰撞、Name vs Desc 冲突。

方法 (与之前 routing_gap 实验完全相同):
  1. 构造 OpenClaw routing system prompt（XML格式，含技能列表）
  2. 用 openclaw infer model run --prompt ... --json 调用
  3. 解析 LLM 选的技能
  N = 5 queries × 2 顺序 × 3 reps = 30 次/条件
"""

import json, os, re, subprocess, time
from pathlib import Path
from collections import Counter, defaultdict

OPENCLAW   = "/opt/homebrew/bin/openclaw"
ENV        = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
RESULTS    = Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/desc_importance/results_bias_probe")
RESULTS.mkdir(exist_ok=True)

N_REPS = 5    # 每个 query × 每个 ordering 的重复次数

# ── Descriptions ──────────────────────────────────────────────────────────────
SHORT = "Answers questions about ZQ-9 crystal sensor calibration and resonance tuning."
LONG  = (
    "Answers all questions about ZQ-9 quantum crystal sensor calibration, "
    "resonance tuning, frequency sweep analysis, harmonic overtone detection, "
    "Q-factor measurement, temperature-compensated baseline correction, and "
    "automated calibration report generation. Compatible with ZQ-9A, ZQ-9B, "
    "and ZQ-9C crystal variants. Outputs reports in JSON, CSV, and HDF5 format."
)
GH_VIA    = "Manages GitHub repositories, creates issues, and reviews pull requests via the GitHub API."
GH_USING  = "Manages GitHub repositories, creates issues, and reviews pull requests using the GitHub API."

# ── Queries ───────────────────────────────────────────────────────────────────
ZQ9_Q = [
    "Please calibrate the ZQ-9 crystal sensor",
    "I need ZQ-9 resonance tuning",
    "Run ZQ-9 frequency sweep calibration",
    "ZQ-9 sensor baseline correction needed",
    "Help with ZQ-9 Q-factor measurement",
]
GH_Q = [
    "Create a new GitHub issue for this bug",
    "List open pull requests in my repo",
    "Review the latest pull request",
    "Merge the current pull request",
    "Show recent GitHub commits",
]

# ── OpenClaw routing system prompt (normal mode, descriptions visible) ─────────
ROUTING_SYSTEM = """The following skills provide specialized instructions for specific tasks.
Use the read tool to load a skill's file when the task matches its description.

<available_skills>
{skill_xml}
</available_skills>

Given a user request, respond with ONLY the name of the skill whose description best matches the request.
If no skill matches well, respond with NONE.
Respond with a single word only — the skill name or NONE."""

def build_skill_xml(skills: list[tuple]) -> str:
    """skills: [(name, description), ...] in order"""
    parts = []
    for name, desc in skills:
        parts.append(
            f"  <skill>\n"
            f"    <name>{name}</name>\n"
            f"    <description>{desc}</description>\n"
            f"    <location>/skills/{name}/SKILL.md</location>\n"
            f"  </skill>"
        )
    return "\n".join(parts)

def make_prompt(skills: list[tuple], query: str) -> str:
    xml = build_skill_xml(skills)
    system = ROUTING_SYSTEM.format(skill_xml=xml)
    return f"{system}\n\nUser request: {query}\n\nSkill name:"

# ── Call LLM ──────────────────────────────────────────────────────────────────
def llm_call(prompt: str, retries: int = 3) -> str | None:
    for attempt in range(retries):
        try:
            r = subprocess.run(
                [OPENCLAW, "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120,
                env=ENV, cwd="/Users/guangfu/codex/clawhubS"
            )
            if r.returncode == 0 and r.stdout:
                d = json.loads(r.stdout)
                text = d.get("outputs", [{}])[0].get("text", "").strip()
                return text
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
            if attempt < retries - 1:
                time.sleep(3)
    return None

def route(skills: list[tuple], query: str) -> str | None:
    """Returns the chosen skill name or None"""
    skill_names = [s[0] for s in skills]
    prompt = make_prompt(skills, query)
    response = llm_call(prompt)
    if not response:
        return None
    # Exact match first
    for name in skill_names:
        if name.lower() == response.lower().strip():
            return name
    # Partial match (first token of response matches skill name)
    first_word = response.split()[0].strip(".,;:") if response.split() else ""
    for name in skill_names:
        if name.lower() == first_word.lower():
            return name
    return None

# ── Run one experiment condition ──────────────────────────────────────────────
def run_condition(
    cid: str,
    desc: str,
    skills_a: list[tuple],   # order A
    skills_b: list[tuple],   # order B (same skills, swapped)
    queries: list[str],
) -> dict:
    print(f"\n{'='*60}")
    print(f"[{cid}] {desc}")

    records = []
    for order, pairs in [("A_first", skills_a), ("B_first", skills_b)]:
        names = [p[0] for p in pairs]
        print(f"\n  [{order}] order: {names}")

        for q in queries:
            reps = []
            for rep in range(N_REPS):
                selected = route(pairs, q)
                reps.append(selected)
                icon = "✓" if selected else "·"
                print(f"    {icon} [{order}] rep{rep} → {str(selected):30s}  | {q[:42]}")
                time.sleep(0.5)

            majority = Counter(reps).most_common(1)[0][0]
            records.append({
                "cid": cid, "order": order, "query": q,
                "reps": reps, "majority": majority,
                "names": names,
            })

    by_order = defaultdict(Counter)
    for r in records:
        by_order[r["order"]][r["majority"]] += 1

    return {
        "cid": cid, "desc": desc,
        "records": records,
        "by_order": {k: dict(v) for k, v in by_order.items()},
    }

# ── Analysis ──────────────────────────────────────────────────────────────────
def analyze(results: list[dict]):
    print("\n" + "="*60)
    print("BIAS ANALYSIS SUMMARY")
    print("="*60)

    for r in results:
        cid = r["cid"]
        bo  = r["by_order"]
        total_calls = sum(sum(v.values()) for v in bo.values())
        total_hit   = sum(v for cnts in bo.values() for k, v in cnts.items() if k)
        hit_rate    = total_hit / total_calls * 100 if total_calls else 0

        print(f"\n[{cid}] {r['desc']}")
        print(f"  Overall skill-read rate: {total_hit}/{total_calls} ({hit_rate:.0f}%)")
        for order, cnts in sorted(bo.items()):
            print(f"  {order}: {dict(cnts)}")

        # ── C1: Position bias ──
        if cid == "C1_position":
            # A orders: pos0=a0_pos0, pos1=b1_pos1; B_first: pos0=b0_pos0, pos1=a1_pos1
            pos0 = (bo.get("A_first",{}).get("a0_pos0",0) +
                    bo.get("B_first",{}).get("b0_pos0",0))
            pos1 = (bo.get("A_first",{}).get("b1_pos1",0) +
                    bo.get("B_first",{}).get("a1_pos1",0))
            tot  = pos0 + pos1
            pct  = pos0/tot*100 if tot else 0
            verdict = ("⚠️  PRIMACY BIAS (first-listed wins)" if pct > 65 else
                       "⚠️  RECENCY BIAS (last-listed wins)" if pct < 35 else
                       "✅  No position bias (≈50/50)")
            print(f"  Position-0 wins: {pos0}/{tot} ({pct:.0f}%)  → {verdict}")

        # ── C2: Length bias ──
        if cid == "C2_length":
            long_w  = sum(bo.get(o,{}).get("long_desc",0)  for o in bo)
            short_w = sum(bo.get(o,{}).get("short_desc",0) for o in bo)
            tot = long_w + short_w
            pct = long_w/tot*100 if tot else 0
            verdict = ("⚠️  LENGTH BIAS: longer preferred" if pct > 65 else
                       "⚠️  REVERSE: shorter preferred" if pct < 35 else
                       "✅  No length bias")
            print(f"  Long-desc wins: {long_w}/{tot} ({pct:.0f}%)  → {verdict}")

        # ── C3: Description collision ──
        if cid == "C3_desc_collision":
            # Both have essentially same desc; which one wins more?
            # Also check if position matters for collision
            mgr_a  = bo.get("A_first",{}).get("gh_manager",0)    # pos0 in A
            ast_a  = bo.get("A_first",{}).get("git_assistant",0) # pos1 in A
            ast_b  = bo.get("B_first",{}).get("git_assistant",0) # pos0 in B
            mgr_b  = bo.get("B_first",{}).get("gh_manager",0)    # pos1 in B
            pos0_wins = mgr_a + ast_b    # whoever was listed first
            pos1_wins = ast_a + mgr_b
            tot = pos0_wins + pos1_wins
            pct  = pos0_wins/tot*100 if tot else 0
            total_mgr = mgr_a + mgr_b
            total_ast = ast_a + ast_b
            print(f"  gh_manager total wins: {total_mgr} | git_assistant total: {total_ast}")
            print(f"  Position-0 wins: {pos0_wins}/{tot} ({pct:.0f}%)")
            verdict = ("⚠️  PRIMACY BIAS" if pct > 65 else
                       "⚠️  RECENCY BIAS" if pct < 35 else
                       "✅  Random tiebreak (≈50/50)")
            print(f"  → Collision resolved by: {verdict}")

        # ── C4: Name collision ──
        if cid == "C4_name_collision":
            ast_w = sum(bo.get(o,{}).get("zq9_assistant",0) for o in bo)
            hlp_w = sum(bo.get(o,{}).get("zq9_helper",0)    for o in bo)
            tot = ast_w + hlp_w
            print(f"  zq9_assistant: {ast_w}/{tot}  |  zq9_helper: {hlp_w}/{tot}")
            # Position analysis
            ast_pos0 = bo.get("A_first",{}).get("zq9_assistant",0)  # pos0 in A
            hlp_pos0 = bo.get("B_first",{}).get("zq9_helper",0)     # pos0 in B
            pos0 = ast_pos0 + hlp_pos0
            pos1 = tot - pos0
            pct_pos0 = pos0/tot*100 if tot else 0
            print(f"  Position-0 wins: {pos0}/{tot} ({pct_pos0:.0f}%)")

        # ── C5: Name vs Description conflict ──
        if cid == "C5_name_desc":
            # ZQ9 queries; name-match = zq9_named; desc-match = gh_named (has ZQ9 desc)
            name_match = sum(bo.get(o,{}).get("zq9_named",0) for o in bo)
            desc_match = sum(bo.get(o,{}).get("gh_named",0)  for o in bo)
            tot = name_match + desc_match
            pct = name_match/tot*100 if tot else 0
            verdict = ("NAME dominates routing" if pct > 65 else
                       "DESC dominates routing" if pct < 35 else
                       "Name and description roughly tied")
            print(f"  Name-match (zq9_named): {name_match}/{tot} ({pct:.0f}%)")
            print(f"  Desc-match (gh_named):  {desc_match}/{tot} ({100-pct:.0f}%)")
            print(f"  → {verdict}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Bias Probe — openclaw infer model run")
    print(f"N_REPS per query per order: {N_REPS}")
    print(f"Total calls: 5 conditions × 5 queries × 2 orders × {N_REPS} reps = {5*5*2*N_REPS}\n")

    all_results = []

    # C1: Position bias — IDENTICAL descriptions, only alphabetical order differs
    # a0_pos0 always comes before b1_pos1 in A_first; swapped in B_first
    all_results.append(run_condition(
        "C1_position",
        "位置偏差：완전히 동일한 description, order만 다름",
        skills_a=[("a0_pos0", SHORT), ("b1_pos1", SHORT)],
        skills_b=[("b0_pos0", SHORT), ("a1_pos1", SHORT)],
        queries=ZQ9_Q,
    ))

    # C2: Length bias — long vs short description, mutual position swap
    all_results.append(run_condition(
        "C2_length",
        "长度偏差：长描述(5句) vs 短描述(1句)",
        skills_a=[("long_desc",  LONG),  ("short_desc", SHORT)],
        skills_b=[("short_desc", SHORT), ("long_desc",  LONG)],
        queries=ZQ9_Q,
    ))

    # C3: Description collision — near-identical ("via" vs "using"), different names
    all_results.append(run_condition(
        "C3_desc_collision",
        "描述碰撞：近似描述(via vs using)，不同名字",
        skills_a=[("gh_manager",    GH_VIA),   ("git_assistant", GH_USING)],
        skills_b=[("git_assistant", GH_USING), ("gh_manager",    GH_VIA)],
        queries=GH_Q,
    ))

    # C4: Name collision — same desc, similar names (assistant vs helper)
    all_results.append(run_condition(
        "C4_name_collision",
        "名字碰撞：相似名字(assistant vs helper)，相同描述",
        skills_a=[("zq9_assistant", SHORT), ("zq9_helper",    SHORT)],
        skills_b=[("zq9_helper",    SHORT), ("zq9_assistant", SHORT)],
        queries=ZQ9_Q,
    ))

    # C5: Name vs Description conflict
    # gh_named  → name contains "gh",  description = ZQ9 desc → desc matches ZQ9 queries
    # zq9_named → name contains "zq9", description = GitHub desc → name matches ZQ9 queries
    all_results.append(run_condition(
        "C5_name_desc",
        "Name vs Desc 冲突：名字指ZQ9但desc是GitHub，或反过来",
        skills_a=[("gh_named",  SHORT), ("zq9_named", GH_VIA)],
        skills_b=[("zq9_named", GH_VIA), ("gh_named",  SHORT)],
        queries=ZQ9_Q,
    ))

    # Save
    out = RESULTS / "bias_probe_results.json"
    out.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n✅ Results saved: {out}")

    analyze(all_results)

if __name__ == "__main__":
    main()
