#!/usr/bin/env python3
"""
run_bias_experiment.py
======================
使用独立的 --profile bias2 进行偏差实验。
测试：描述碰撞、位置偏差、长度偏差
"""
import json, os, re, subprocess, time, pathlib, shutil
from collections import Counter, defaultdict

PATH_ENV  = "/opt/homebrew/bin:" + os.environ.get("PATH", "")
ENV       = {**os.environ, "PATH": PATH_ENV}
OPENCLAW  = "/opt/homebrew/bin/openclaw"
PROFILE   = "bias2"
AGENT     = "experiment"
EXP_WS    = pathlib.Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/bias_probe_ws")
SESS_DIR  = pathlib.Path.home() / f".openclaw-{PROFILE}/agents/{AGENT}/sessions"
RESULTS   = pathlib.Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/desc_importance/results_bias_probe")
RESULTS.mkdir(exist_ok=True)

SKILLS = EXP_WS / ".agents" / "skills"

SHORT = "Answers questions about ZQ-9 crystal sensor calibration and resonance tuning."
LONG  = (
    "Answers all questions about ZQ-9 quantum crystal sensor calibration, "
    "resonance tuning, frequency sweep analysis, harmonic overtone detection, "
    "Q-factor measurement, temperature-compensated baseline correction, and "
    "automated calibration report generation. Compatible with ZQ-9A, ZQ-9B, "
    "and ZQ-9C variants. Outputs JSON, CSV, HDF5 reports."
)
GH_VIA    = "Manages GitHub repositories, creates issues, and reviews pull requests via the GitHub API."
GH_USING  = "Manages GitHub repositories, creates issues, and reviews pull requests using the GitHub API."

ZQ9_QUERIES = [
    "Please calibrate the ZQ-9 crystal sensor",
    "I need ZQ-9 resonance tuning help",
    "Run a ZQ-9 frequency sweep calibration",
    "ZQ-9 sensor setup and calibration",
    "Help with ZQ-9 baseline correction",
]
GH_QUERIES = [
    "Create a new GitHub issue for this bug",
    "List all open pull requests in my repo",
    "Review the latest pull request",
    "Merge the current pull request",
    "Show recent GitHub commits",
]

# ── Skill management ──────────────────────────────────────────────────────────
def clear_skills():
    if SKILLS.exists():
        shutil.rmtree(SKILLS)
    SKILLS.mkdir(parents=True)

def install(name: str, desc: str):
    d = SKILLS / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\nTRIGGERED_{name.upper().replace('-','_')}\n"
    )

def count_desc_chars() -> int:
    total = 0
    for md in SKILLS.rglob("SKILL.md"):
        m = re.search(r"description:\s*(.+)", md.read_text())
        if m:
            total += len(m.group(1).strip())
    return total

# ── Session management ────────────────────────────────────────────────────────
def get_session_file() -> pathlib.Path | None:
    if not SESS_DIR.exists():
        return None
    files = sorted(
        [f for f in SESS_DIR.glob("*.jsonl") if "checkpoint" not in f.name],
        key=lambda f: f.stat().st_mtime, reverse=True
    )
    return files[0] if files else None

def clear_session():
    """Truncate session + remove cached skill snapshot."""
    sj = SESS_DIR / "sessions.json"
    if sj.exists():
        sj.write_text("{}")
    sf = get_session_file()
    if sf:
        sf.write_text("")

def parse_tool_reads(content: str, skill_names: list[str]) -> str | None:
    """Parse session jsonl content for read() calls, return first matched skill name."""
    for line in content.splitlines():
        try:
            d = json.loads(line)
            msg = d.get("message", {})
            for blk in (msg.get("content") or []):
                if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("name") == "read":
                    path = (blk.get("input") or {}).get("path", "")
                    for name in skill_names:
                        if name in path:
                            return name
        except:
            pass
    return None

# ── Run one turn ──────────────────────────────────────────────────────────────
def run_turn(query: str, skill_names: list[str]) -> dict:
    clear_session()
    try:
        r = subprocess.run(
            [OPENCLAW, "--profile", PROFILE, "agent",
             "--agent", AGENT, "--local",
             "--message", query, "--verbose", "on"],
            capture_output=True, text=True, timeout=90,
            cwd=str(EXP_WS), env=ENV
        )
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return {"selected": None, "error": "TIMEOUT"}

    # Try session file first
    sf = get_session_file()
    selected = None
    if sf and sf.exists():
        selected = parse_tool_reads(sf.read_text(), skill_names)

    # Fallback: grep stdout for SKILL.md paths
    if not selected:
        for line in out.splitlines():
            for name in skill_names:
                if name in line:
                    selected = name
                    break
            if selected:
                break

    # Last resort: look for TRIGGERED_ marker in tool results
    if not selected:
        for name in skill_names:
            if f"TRIGGERED_{name.upper().replace('-','_')}" in out:
                selected = name
                break

    reply_lines = [l for l in out.splitlines()
                   if not l.startswith(("[","Config","bundle","web_","Reg","🦞","LLM ")) and l.strip()]
    reply = "\n".join(reply_lines[-2:])

    return {"selected": selected, "reply": reply[:150],
            "rc": r.returncode, "out_len": len(out)}

# ── Experiment runner ─────────────────────────────────────────────────────────
N = 3  # reps per query

def run_condition(cid: str, desc: str, skills: dict, queries: list[str]) -> dict:
    """
    skills: {"A": [(name, desc_str), ...], "B": [(name, desc_str), ...]}
           A and B differ in ORDER or skill content (for position bias: same skills swapped)
    """
    print(f"\n{'='*56}")
    print(f"[{cid}] {desc}")
    records = []

    for order, pairs in skills.items():
        clear_skills()
        names = []
        for name, d in pairs:
            install(name, d)
            names.append(name)

        chars = count_desc_chars()
        mode = "COMPACT⚠️" if chars > 30000 else "normal✅"
        print(f"\n  [{order}] skills={names}  desc_chars={chars}  mode={mode}")

        for q in queries:
            reps = []
            for rep in range(N):
                res = run_turn(q, names)
                sel = res["selected"]
                reps.append(sel)
                icon = "✓" if sel else "·"
                print(f"    {icon} [{order}] rep{rep} → {sel or 'NONE':30s}  | {q[:42]}")
                time.sleep(2)

            majority = Counter(reps).most_common(1)[0][0]
            records.append({
                "cid": cid, "order": order, "query": q,
                "reps": reps, "majority": majority, "names": names,
            })

    clear_skills()

    by_order = defaultdict(Counter)
    for r in records:
        by_order[r["order"]][r["majority"]] += 1

    return {"cid": cid, "desc": desc, "records": records,
            "by_order": {k: dict(v) for k, v in by_order.items()}}

# ── Analysis ──────────────────────────────────────────────────────────────────
def analyze(results: list[dict]):
    print("\n" + "="*56)
    print("BIAS ANALYSIS SUMMARY")
    print("="*56)

    for r in results:
        cid = r["cid"]
        bo  = r["by_order"]
        total_calls = sum(sum(v.values()) for v in bo.values())
        total_hit   = sum(v for cnts in bo.values() for k,v in cnts.items() if k)
        hit_rate    = total_hit / total_calls * 100 if total_calls else 0

        print(f"\n[{cid}] {r['desc']}")
        print(f"  Skill read rate: {total_hit}/{total_calls} ({hit_rate:.0f}%)")
        for order, cnts in sorted(bo.items()):
            print(f"  {order}: {dict(cnts)}")

        if cid == "C1_position":
            # A_first: first = a0_zq9_pos0; B_first: first = b0_zq9_pos0
            pos0_a = bo.get("A_first",{}).get("a0_zq9_pos0",0)
            pos1_a = bo.get("A_first",{}).get("b1_zq9_pos1",0)
            pos0_b = bo.get("B_first",{}).get("b0_zq9_pos0",0)
            pos1_b = bo.get("B_first",{}).get("a1_zq9_pos1",0)
            pos0 = pos0_a + pos0_b
            pos1 = pos1_a + pos1_b
            tot  = pos0 + pos1
            pct  = pos0/tot*100 if tot else 0
            verdict = ("⚠️ PRIMACY BIAS (first-listed preferred)" if pct > 65 else
                       "⚠️ RECENCY BIAS (last-listed preferred)" if pct < 35 else
                       "✅ No position bias")
            print(f"  Position-0 wins: {pos0}/{tot} ({pct:.0f}%)  → {verdict}")

        if cid == "C2_length":
            long_wins  = sum(bo.get(o,{}).get("long_zq9",0)  for o in bo)
            short_wins = sum(bo.get(o,{}).get("short_zq9",0) for o in bo)
            tot = long_wins + short_wins
            pct = long_wins/tot*100 if tot else 0
            verdict = ("⚠️ LENGTH BIAS: longer description preferred" if pct > 65 else
                       "⚠️ REVERSE: shorter preferred" if pct < 35 else
                       "✅ No length bias")
            print(f"  Long wins: {long_wins}/{tot} ({pct:.0f}%)  → {verdict}")

        if cid == "C3_desc_collision":
            # Near-identical desc, different names → which gets selected more?
            mgr = sum(bo.get(o,{}).get("gh_manager",0)    for o in bo)
            ast = sum(bo.get(o,{}).get("git_assistant",0) for o in bo)
            tot = mgr + ast
            print(f"  gh_manager wins: {mgr}/{tot}  git_assistant wins: {ast}/{tot}")
            if tot > 0:
                pct = mgr/tot*100
                verdict = ("⚠️ FIRST-listed wins" if
                           (bo.get("A_first",{}).get("gh_manager",0) >
                            bo.get("A_first",{}).get("git_assistant",0))
                           else "⚠️ SECOND-listed wins (recency)")
                print(f"  first-listed bias: {verdict}")

        if cid == "C5_name_desc":
            name_match = sum(bo.get(o,{}).get("zq9_named",0) for o in bo)
            desc_match = sum(bo.get(o,{}).get("gh_named",0)  for o in bo)
            tot = name_match + desc_match
            if tot:
                verdict = ("NAME wins → name is primary routing signal" if name_match > desc_match else
                           "DESC wins → description is primary routing signal" if desc_match > name_match else
                           "Tied")
                print(f"  Name-match(zq9_named): {name_match}/{tot}")
                print(f"  Desc-match(gh_named):  {desc_match}/{tot}")
                print(f"  → {verdict}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Bias Probe — profile=bias2, workspace=bias_probe_ws")
    print(f"Normal mode threshold: 30,000 chars")
    all_results = []

    # C1: Position bias — same skill descriptions, only alphabetical order differs
    # a0_zq9_pos0 comes before b1_zq9_pos1 in A_first
    # b0_zq9_pos0 comes before a1_zq9_pos1 in B_first
    all_results.append(run_condition(
        "C1_position",
        "位置偏差：完全相同描述，字母序控制前后",
        skills={
            "A_first": [("a0_zq9_pos0", SHORT), ("b1_zq9_pos1", SHORT)],
            "B_first": [("b0_zq9_pos0", SHORT), ("a1_zq9_pos1", SHORT)],
        },
        queries=ZQ9_QUERIES,
    ))

    # C2: Length bias
    all_results.append(run_condition(
        "C2_length",
        "长度偏差：长描述(5句) vs 短描述(1句)，互换位置",
        skills={
            "A_first": [("long_zq9",  LONG),  ("short_zq9", SHORT)],
            "B_first": [("short_zq9", SHORT), ("long_zq9",  LONG)],
        },
        queries=ZQ9_QUERIES,
    ))

    # C3: Description collision — near-identical desc ("via" vs "using"), different names
    all_results.append(run_condition(
        "C3_desc_collision",
        "描述碰撞：近似描述(via vs using)，不同名字",
        skills={
            "A_first": [("gh_manager",    GH_VIA),   ("git_assistant", GH_USING)],
            "B_first": [("git_assistant", GH_USING), ("gh_manager",    GH_VIA)],
        },
        queries=GH_QUERIES,
    ))

    # C4: Name collision — same desc, similar names (assistant vs helper)
    all_results.append(run_condition(
        "C4_name_collision",
        "名字碰撞：相似名字(assistant vs helper)，相同描述",
        skills={
            "A_first": [("zq9_assistant", SHORT), ("zq9_helper",    SHORT)],
            "B_first": [("zq9_helper",    SHORT), ("zq9_assistant", SHORT)],
        },
        queries=ZQ9_QUERIES,
    ))

    # C5: Name vs Description conflict
    # zq9_named: name=zq9, desc=GitHub → desc-mismatch; name matches ZQ9 queries
    # gh_named:  name=gh,  desc=ZQ9   → desc-mismatch; desc matches ZQ9 queries
    all_results.append(run_condition(
        "C5_name_desc",
        "Name vs Desc 冲突：名字指ZQ9但desc是GitHub，或反过来",
        skills={
            "A_first": [("gh_named",  SHORT), ("zq9_named", GH_VIA)],
            "B_first": [("zq9_named", GH_VIA), ("gh_named", SHORT)],
        },
        queries=ZQ9_QUERIES,
    ))

    # Save
    out = RESULTS / "bias_probe_results.json"
    out.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n✅ Results saved: {out}")

    analyze(all_results)

if __name__ == "__main__":
    main()
