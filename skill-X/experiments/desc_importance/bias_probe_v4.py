#!/usr/bin/env python3
"""
bias_probe_v4.py
================
用 main agent (已证明可用) 做偏差实验。
方法: 每次实验前，在 clawhubS .agents/skills/ 里临时写入/替换探测技能，
      清空 main session 快照缓存，运行 agent turn，从 session jsonl 读结果。

重点测试:
  C1: 位置偏差  — 相同文本，字母序前/后
  C2: 描述长度  — 长(8句) vs 短(1句)
  C3: 描述碰撞  — 近似描述(via vs using)，不同名字
  C4: 名字碰撞  — 相似名字(assistant vs helper)，相同描述
  C5: Name vs Desc 冲突
"""

import json, os, re, shutil, subprocess, time, pathlib
from collections import Counter, defaultdict

PATH    = "/opt/homebrew/bin:" + os.environ.get("PATH", "")
ENV     = {**os.environ, "PATH": PATH}
OPENCLAW = "/opt/homebrew/bin/openclaw"
WS      = pathlib.Path("/Users/guangfu/codex/clawhubS")
SKILLS  = WS / ".agents" / "skills"
AGENT   = "main"

RESULTS = WS / "skill-X/experiments/desc_importance/results_bias_probe"
RESULTS.mkdir(exist_ok=True)

# ── Probe skill prefix (easy to find/clean) ──────────────────────────────────
PFX = "_probe_"

SHORT = "Answers questions about ZQ-9 crystal sensor calibration and resonance tuning."
LONG  = (
    "Answers all questions about ZQ-9 quantum crystal sensor calibration, resonance "
    "tuning, frequency sweep analysis, harmonic overtone detection, Q-factor measurement, "
    "temperature-compensated baseline correction, and automated calibration report generation. "
    "Compatible with ZQ-9A, ZQ-9B, and ZQ-9C crystal variants. "
    "Outputs reports in JSON, CSV, and HDF5 format."
)
GH_A = "Manages GitHub repositories, creates issues, and reviews pull requests via the GitHub API."
GH_B = "Manages GitHub repositories, creates issues, and reviews pull requests using the GitHub API."

ZQ9_Q = [
    "Please calibrate the ZQ-9 crystal sensor",
    "I need ZQ-9 resonance tuning",
    "Run a QZ-9 frequency sweep calibration",
    "ZQ-9 sensor calibration please",
    "Help me with ZQ-9 setup",
]
GH_Q = [
    "Create a new GitHub issue for this bug",
    "List open pull requests in my repo",
    "Review the latest pull request",
    "Merge the current pull request",
    "Show recent GitHub commits",
]

# ── Install / cleanup probe skills ────────────────────────────────────────────
def install(name: str, desc: str, body: str = ""):
    d = SKILLS / name
    d.mkdir(parents=True, exist_ok=True)
    body = body or f"When used, report: TRIGGERED_{name.upper().replace('-','_')}"
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n\n{body}\n"
    )

def cleanup():
    for d in SKILLS.iterdir():
        if d.is_dir() and d.name.startswith(PFX):
            shutil.rmtree(d)

# ── Session management ────────────────────────────────────────────────────────
SESS_DIR = pathlib.Path.home() / ".openclaw/agents" / AGENT / "sessions"

def get_session_file() -> pathlib.Path | None:
    files = sorted(SESS_DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    files = [f for f in files if "checkpoint" not in f.name]
    return files[0] if files else None

def clear_skill_snapshot():
    """Delete the cached skill snapshot so openclaw re-reads skills on next turn."""
    sj = SESS_DIR / "sessions.json"
    if sj.exists():
        try:
            d = json.loads(sj.read_text())
            for key in d:
                if "skillsSnapshot" in d[key]:
                    del d[key]["skillsSnapshot"]
            sj.write_text(json.dumps(d))
        except:
            sj.write_text("{}")

def session_offset() -> int:
    sf = get_session_file()
    return sf.stat().st_size if sf and sf.exists() else 0

def read_new_tool_calls(offset: int) -> list[str]:
    """Read tool_use 'read' calls added since offset."""
    sf = get_session_file()
    if not sf or not sf.exists():
        return []
    content = sf.read_text()
    new_part = content[offset:]
    paths = []
    for line in new_part.splitlines():
        try:
            msg = json.loads(line).get("message", {})
            for blk in (msg.get("content") or []):
                if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("name") == "read":
                    p = (blk.get("input") or {}).get("path", "")
                    if p:
                        paths.append(p)
        except:
            pass
    return paths

# ── Run one agent turn ────────────────────────────────────────────────────────
def run_turn(query: str, probe_names: list[str], timeout: int = 60) -> dict:
    clear_skill_snapshot()
    offset = session_offset()

    try:
        r = subprocess.run(
            [OPENCLAW, "agent", "--agent", AGENT, "--local",
             "--message", query, "--verbose", "on"],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(WS), env=ENV
        )
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return {"selected": None, "paths": [], "reply": "TIMEOUT"}

    # Session jsonl paths (most reliable)
    paths = read_new_tool_calls(offset)

    # Fallback: grep output directly
    if not paths:
        paths = re.findall(r"[\w./_-]+(?:SKILL\.md|/skills/\w+)", out)

    # Map to probe skill name
    selected = None
    for path in paths:
        for name in probe_names:
            if name in path:
                selected = name
                break
        if selected:
            break

    # Extract agent reply
    reply_lines = [l for l in out.splitlines()
                   if not l.startswith(("[", "Config", "bundle", "web_", "Reg", "🦞"))
                   and l.strip()]
    reply = "\n".join(reply_lines[-3:])

    return {"selected": selected, "paths": paths[:3], "reply": reply[:200]}

# ── One experiment condition ──────────────────────────────────────────────────
N_REPS = 3

def experiment(eid: str, desc: str,
               skills_a: list[tuple],   # [(name, desc_str), ...]  order A
               skills_b: list[tuple],   # order B (for position swap)
               queries: list[str]) -> dict:
    print(f"\n{'='*58}")
    print(f"[{eid}] {desc}")
    records = []

    for order, pairs in [("A_first", skills_a), ("B_first", skills_b)]:
        # Install probe skills
        cleanup()
        names = []
        for name, d in pairs:
            install(name, d)
            names.append(name)

        # Verify normal mode
        total_desc = sum(len(d) for _, d in pairs)
        mode = "COMPACT" if total_desc > 30000 else "normal"
        print(f"\n  [{order}] {names}  |  desc_chars={total_desc}  mode={mode}")

        for q in queries:
            rep_results = []
            for rep in range(N_REPS):
                res = run_turn(q, names)
                sel = res["selected"]
                rep_results.append(sel)
                icon = "✓" if sel else "·"
                print(f"    {icon} rep{rep} {order} → {sel or 'NONE':30s}  q={q[:40]}")
                time.sleep(2)

            majority = Counter(rep_results).most_common(1)[0][0]
            records.append({
                "eid": eid, "order": order, "query": q,
                "reps": rep_results, "majority": majority,
                "names": names,
            })

    cleanup()

    by_order = defaultdict(Counter)
    for r in records:
        by_order[r["order"]][r["majority"]] += 1

    return {"eid": eid, "desc": desc,
            "records": records,
            "by_order": {k: dict(v) for k, v in by_order.items()}}

# ── Print analysis ────────────────────────────────────────────────────────────
def analyze(results: list[dict]):
    print("\n" + "="*58)
    print("ANALYSIS")
    print("="*58)
    for r in results:
        eid = r["eid"]
        bo  = r["by_order"]
        print(f"\n[{eid}] {r['desc']}")
        for order, cnts in sorted(bo.items()):
            print(f"  {order}: {dict(cnts)}")

        # ── Position bias: did position-0 skill get selected more? ──
        if eid in ("C1_position",):
            # A_first: _probe_0_alpha = pos0; B_first: _probe_0_beta = pos0
            pos0 = (bo.get("A_first",{}).get(PFX+"0_alpha",0) +
                    bo.get("B_first",{}).get(PFX+"0_beta",0))
            pos1 = (bo.get("A_first",{}).get(PFX+"1_beta",0) +
                    bo.get("B_first",{}).get(PFX+"1_alpha",0))
            tot = pos0+pos1
            pct = pos0/tot*100 if tot else 0
            verdict = ("⚠️  PRIMACY BIAS" if pct>65 else
                       "⚠️  RECENCY BIAS" if pct<35 else "✅  No bias")
            print(f"  Position-0 wins: {pos0}/{tot} ({pct:.0f}%)  → {verdict}")

        if eid == "C2_length":
            # A_first: 0_long=pos0; B_first: 0_short=pos0, so long is pos1
            long_w  = (bo.get("A_first",{}).get(PFX+"0_long",0) +
                       bo.get("B_first",{}).get(PFX+"1_long",0))
            short_w = (bo.get("A_first",{}).get(PFX+"1_short",0) +
                       bo.get("B_first",{}).get(PFX+"0_short",0))
            tot = long_w+short_w
            pct = long_w/tot*100 if tot else 0
            verdict = ("⚠️  LENGTH BIAS: longer preferred" if pct>65 else
                       "⚠️  REVERSE: shorter preferred" if pct<35 else "✅  No length bias")
            print(f"  Long wins: {long_w}/{tot} ({pct:.0f}%)  → {verdict}")

        if eid == "C5_name_desc":
            # ZQ-9 queries → name-match=zq9_named; desc-match=gh_named
            nm = sum(bo.get(o,{}).get(PFX+"zq9_named",0) for o in bo)
            dm = sum(bo.get(o,{}).get(PFX+"gh_named",0)  for o in bo)
            tot = nm+dm
            if tot:
                verdict = ("NAME wins" if nm>dm else "DESC wins" if dm>nm else "Tied")
                print(f"  Name-match: {nm}/{tot} | Desc-match: {dm}/{tot}  → {verdict}")

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("Bias Probe v4 — main agent, probe skills in .agents/skills/")
    cleanup()
    all_results = []

    # C1: Position bias (same desc, names differ only in 0_/1_ prefix → alphabetical order)
    all_results.append(experiment(
        "C1_position",
        "位置偏差：相同描述，字母序 0_ 在前 vs 1_ 在后",
        skills_a=[(PFX+"0_alpha", SHORT), (PFX+"1_beta",  SHORT)],
        skills_b=[(PFX+"0_beta",  SHORT), (PFX+"1_alpha", SHORT)],
        queries=ZQ9_Q,
    ))

    # C2: Length bias
    all_results.append(experiment(
        "C2_length",
        "描述长度偏差：长描述(5句) vs 短描述(1句)",
        skills_a=[(PFX+"0_long",  LONG),  (PFX+"1_short", SHORT)],
        skills_b=[(PFX+"0_short", SHORT), (PFX+"1_long",  LONG)],
        queries=ZQ9_Q,
    ))

    # C3: Description collision (near-identical desc, different names)
    all_results.append(experiment(
        "C3_desc_collision",
        "描述碰撞：近似描述(via vs using)，不同名字",
        skills_a=[(PFX+"gh_manager", GH_A), (PFX+"git_assistant", GH_B)],
        skills_b=[(PFX+"git_assistant", GH_B), (PFX+"gh_manager", GH_A)],
        queries=GH_Q,
    ))

    # C4: Name collision (same desc, similar names: assistant vs helper)
    all_results.append(experiment(
        "C4_name_collision",
        "名字碰撞：相似名字(assistant vs helper)，相同描述",
        skills_a=[(PFX+"zq9_assistant", SHORT), (PFX+"zq9_helper", SHORT)],
        skills_b=[(PFX+"zq9_helper",    SHORT), (PFX+"zq9_assistant", SHORT)],
        queries=ZQ9_Q,
    ))

    # C5: Name vs Desc conflict
    # gh_named: name=gh_, desc=zq9 desc → desc matches ZQ9 queries
    # zq9_named: name=zq9_, desc=gh desc → name matches ZQ9 queries
    all_results.append(experiment(
        "C5_name_desc",
        "Name vs Desc 冲突：name指ZQ9但desc是GitHub(或反过来)",
        skills_a=[(PFX+"gh_named",  SHORT), (PFX+"zq9_named", GH_A)],
        skills_b=[(PFX+"zq9_named", GH_A),  (PFX+"gh_named",  SHORT)],
        queries=ZQ9_Q,   # ZQ9 queries: desc-match=gh_named, name-match=zq9_named
    ))

    # Save
    out = RESULTS / "bias_v4_results.json"
    out.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\nResults → {out}")

    analyze(all_results)

if __name__ == "__main__":
    main()
