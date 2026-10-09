#!/usr/bin/env python3
"""
bias_probe_real.py  (v3)
========================
用真实 openclaw agent --agent main --local 测试 skill 选择偏差。
关键修复: 每次 agent turn 用新的 --session-id (UUID)，保证空历史，
         agent 必须依据 system prompt 选择技能。
检测: 从 stdout+stderr 里找 tool=read 行和 _bp_ 路径。

5个条件:
  C1: 位置偏差 — 相同技能互换顺序
  C2: 长度偏差 — 描述长 vs 短
  C3: 名字碰撞 — 相似名字，相同描述
  C4: 描述碰撞 — 名字不同，描述近似
  C5: Name vs Desc 冲突
"""

import os, re, json, time, shutil, subprocess, uuid
from pathlib import Path
from collections import Counter, defaultdict

OPENCLAW   = "/opt/homebrew/bin/openclaw"
WORKSPACE  = Path("/Users/guangfu/codex/clawhubS")
SKILLS_DIR = WORKSPACE / ".agents" / "skills"
RESULTS_DIR = WORKSPACE / "skill-X" / "experiments" / "desc_importance" / "results_bias_probe"
RESULTS_DIR.mkdir(exist_ok=True)

PROBE_PREFIX = "_bp_"

# ─── Skill 内容生成 ────────────────────────────────────────────────────────────
def make_skill_md(name: str, description: str, trigger_word: str) -> str:
    """trigger_word 是 body 里独一无二的标识词，方便从 log 看哪个被 read"""
    return f"""---
name: {name}
description: {description}
---

# {name}

{trigger_word}

Report exactly: "{trigger_word}_INVOKED" when this skill is used.
"""

# ─── 安装/卸载 ─────────────────────────────────────────────────────────────────
def install_skill(name: str, description: str, trigger: str) -> Path:
    d = SKILLS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    md.write_text(make_skill_md(name, description, trigger))
    return md

def cleanup():
    removed = []
    for d in SKILLS_DIR.iterdir():
        if d.is_dir() and d.name.startswith(PROBE_PREFIX):
            shutil.rmtree(d)
            removed.append(d.name)
    if removed:
        print(f"  [cleanup] removed: {removed}")

# ─── gateway.log 解析 ─────────────────────────────────────────────────────────
def get_log_size() -> int:
    try: return GATEWAY_LOG.stat().st_size
    except: return 0

def get_last_read_calls(since_offset: int) -> list[str]:
    """
    从 gateway.log 的 since_offset 字节位置起，
    提取所有 functions.read 的 path 参数。
    """
    paths = []
    try:
        with open(GATEWAY_LOG, "r", errors="replace") as f:
            f.seek(since_offset)
            new_content = f.read()

        # 两种格式:
        # 1. {"tool_uses":[{"recipient_name":"functions.read","parameters":{"path":"..."}},...]}
        # 2. plain log lines with SKILL.md paths
        matches = re.findall(
            r'"recipient_name"\s*:\s*"functions\.read"[^}]*"path"\s*:\s*"([^"]+)"',
            new_content
        )
        paths.extend(matches)

        # Also catch lines like "- skillname: /path/SKILL.md"
        skill_lines = re.findall(r'-\s+\S+:\s+(/[^\s]+SKILL\.md)', new_content)
        paths.extend(skill_lines)

    except Exception as e:
        print(f"  [log read error] {e}")
    return paths

ENV = os.environ.copy()
ENV["PATH"] = "/opt/homebrew/bin:" + ENV.get("PATH", "")

def run_turn(query: str, skill_names: list[str]) -> dict:
    """
    Run one openclaw agent turn with a FRESH session (random UUID),
    so agent has zero history and MUST read from skills system prompt.
    Returns which probe skill was selected.
    """
    # Generate fresh session ID so agent starts with empty history
    session_id = f"bias-probe-{uuid.uuid4().hex[:12]}"
    out_file = RESULTS_DIR / f"turn_{session_id}.txt"

    try:
        r = subprocess.run(
            [OPENCLAW, "agent", "--agent", "main", "--local",
             "--session-id", session_id,
             "--message", query,
             "--verbose", "on"],
            capture_output=True, text=True, timeout=90,
            cwd=str(WORKSPACE), env=ENV
        )
        combined = r.stdout + r.stderr
        out_file.write_text(combined)
    except subprocess.TimeoutExpired:
        return {"selected": None, "paths": [], "error": "TIMEOUT"}
    except Exception as e:
        return {"selected": None, "paths": [], "error": str(e)}

    # Detect skill from output
    # Pattern 1: verbose log line "tool=read"
    # Pattern 2: any mention of _bp_ skill path in logs
    # Pattern 3: agent text output containing trigger word SKILL_*_INVOKED
    paths = re.findall(r'tool=read[^\n]*|_bp_[\w]+/SKILL\.md|_bp_[\w]+/SKILL', combined)
    
    # Also look for the skill name in [agent/embedded] tool log lines
    skill_reads = re.findall(
        r'embedded run tool (?:start|end):.*?tool=read.*?toolCallId=([^\n]+)',
        combined
    )
    # And look anywhere for our probe skill names
    for name in skill_names:
        short = name.replace(PROBE_PREFIX, "")
        if name in combined or short in combined:
            paths.append(name)

    # Map paths → probe skill name
    selected = None
    for path in paths:
        for name in skill_names:
            if name in path or name.replace(PROBE_PREFIX, "") in path:
                selected = name
                break
        if selected:
            break

    # Last resort: check if agent body output contains our trigger words
    # Extract last non-diagnostic line
    agent_output_lines = [
        l for l in combined.splitlines()
        if not l.startswith(("[", "Config", "bundle", "web_", "Registered", "🦞"))
        and l.strip()
    ]
    agent_reply = "\n".join(agent_output_lines[-5:]) if agent_output_lines else ""
    
    if not selected:
        for name in skill_names:
            trigger = name.replace(PROBE_PREFIX, "").replace("_", "_").upper()
            # Check if agent reply or any log line mentions this skill's trigger
            if f"SKILL_{trigger.split('_')[-1]}_" in combined.upper():
                selected = name
                break

    return {
        "selected": selected,
        "paths": paths[:5],
        "agent_reply": agent_reply[-200:],
        "session_id": session_id,
        "error": r.returncode if r.returncode != 0 else "",
    }

# ─── Descriptions ─────────────────────────────────────────────────────────────
SHORT_DESC = "Handles ZQ-9 crystal resonance calibration requests."
LONG_DESC = (
    "Handles ZQ-9 crystal resonance calibration requests. "
    "Supports multi-phase resonance sweep, frequency lock-in detection, "
    "harmonic overtone analysis, temperature-compensated baseline correction, "
    "real-time Q-factor computation, and automated calibration report generation. "
    "Compatible with ZQ-9A, ZQ-9B, ZQ-9C crystal variants. "
    "Outputs calibration coefficients in JSON, CSV, and HDF5 formats."
)

GITHUB_DESC_A = "Manage GitHub repositories, issues, and pull requests via the GitHub API."
GITHUB_DESC_B = "Manage GitHub repositories, issues, and pull requests through the GitHub API."

# ─── Conditions ───────────────────────────────────────────────────────────────
QUERIES_CRYSTAL = [
    "Please calibrate the ZQ-9 crystal resonance sensor",
    "Run ZQ-9 calibration now",
    "I need ZQ-9 resonance calibration",
    "Calibrate my ZQ-9 crystal",
    "ZQ-9 sensor needs calibration",
]

QUERIES_GITHUB = [
    "Create a new GitHub issue",
    "List open pull requests in my repository",
    "Review the latest pull request comments",
    "Merge the current pull request",
    "Check recent commits on GitHub",
]

CONDITIONS = [
    {
        "id": "C1_position",
        "desc": "位置偏差：相同描述，名字按字母序决定前后顺序",
        "skills_A_first": [
            (PROBE_PREFIX+"0_alpha", SHORT_DESC, "SKILL_ALPHA"),
            (PROBE_PREFIX+"1_beta",  SHORT_DESC, "SKILL_BETA"),
        ],
        "skills_B_first": [
            (PROBE_PREFIX+"0_beta",  SHORT_DESC, "SKILL_BETA"),
            (PROBE_PREFIX+"1_alpha", SHORT_DESC, "SKILL_ALPHA"),
        ],
        "queries": QUERIES_CRYSTAL,
        "pos0_label": "alpha",  # which name is in position 0
        "pos1_label": "beta",
    },
    {
        "id": "C2_length",
        "desc": "长度偏差：同主题，长描述 vs 短描述",
        "skills_A_first": [
            (PROBE_PREFIX+"0_long",  LONG_DESC,  "SKILL_LONG"),
            (PROBE_PREFIX+"1_short", SHORT_DESC, "SKILL_SHORT"),
        ],
        "skills_B_first": [
            (PROBE_PREFIX+"0_short", SHORT_DESC, "SKILL_SHORT"),
            (PROBE_PREFIX+"1_long",  LONG_DESC,  "SKILL_LONG"),
        ],
        "queries": QUERIES_CRYSTAL,
        "pos0_label": "long",
        "pos1_label": "short",
    },
    {
        "id": "C3_name_collision",
        "desc": "名字碰撞：相似名字（assistant vs helper），相同描述",
        "skills_A_first": [
            (PROBE_PREFIX+"0_assistant", SHORT_DESC, "SKILL_ASSISTANT"),
            (PROBE_PREFIX+"1_helper",    SHORT_DESC, "SKILL_HELPER"),
        ],
        "skills_B_first": [
            (PROBE_PREFIX+"0_helper",    SHORT_DESC, "SKILL_HELPER"),
            (PROBE_PREFIX+"1_assistant", SHORT_DESC, "SKILL_ASSISTANT"),
        ],
        "queries": QUERIES_CRYSTAL,
        "pos0_label": "assistant",
        "pos1_label": "helper",
    },
    {
        "id": "C4_desc_collision",
        "desc": "描述碰撞：近似描述，不同名字（github_manager vs git_assistant）",
        "skills_A_first": [
            (PROBE_PREFIX+"0_github_manager", GITHUB_DESC_A, "SKILL_GITHUB_MGR"),
            (PROBE_PREFIX+"1_git_assistant",  GITHUB_DESC_B, "SKILL_GIT_ASST"),
        ],
        "skills_B_first": [
            (PROBE_PREFIX+"0_git_assistant",  GITHUB_DESC_B, "SKILL_GIT_ASST"),
            (PROBE_PREFIX+"1_github_manager", GITHUB_DESC_A, "SKILL_GITHUB_MGR"),
        ],
        "queries": QUERIES_GITHUB,
        "pos0_label": "github_manager",
        "pos1_label": "git_assistant",
    },
    {
        "id": "C5_name_desc_conflict",
        "desc": "Name vs Desc 冲突：名字=crystal/描述=github（A），名字=github/描述=crystal（B）",
        "skills_A_first": [
            # A: name=crystal, desc=github (name misleads)
            (PROBE_PREFIX+"0_crystal_named", GITHUB_DESC_A, "SKILL_CRYSTAL_NAMED"),
            # B: name=github, desc=crystal (desc misleads)
            (PROBE_PREFIX+"1_github_named",  SHORT_DESC,    "SKILL_GITHUB_NAMED"),
        ],
        "skills_B_first": [
            (PROBE_PREFIX+"0_github_named",  SHORT_DESC,    "SKILL_GITHUB_NAMED"),
            (PROBE_PREFIX+"1_crystal_named", GITHUB_DESC_A, "SKILL_CRYSTAL_NAMED"),
        ],
        "queries": QUERIES_CRYSTAL,  # crystal queries → which wins: name-match or desc-match?
        "pos0_label": "crystal_named",
        "pos1_label": "github_named",
    },
]

# ─── Main ─────────────────────────────────────────────────────────────────────
def run_condition(cond: dict) -> dict:
    cid   = cond["id"]
    print(f"\n{'='*60}")
    print(f"[{cid}] {cond['desc']}")

    records = []

    for order_label, skill_defs in [
        ("A_first", cond["skills_A_first"]),
        ("B_first", cond["skills_B_first"]),
    ]:
        cleanup()
        skill_names = []
        for (name, desc, trigger) in skill_defs:
            install_skill(name, desc, trigger)
            skill_names.append(name)
        print(f"\n  [{order_label}] installed: {skill_names}")

        for q in cond["queries"]:
            print(f"    Q: {q[:55]}...", end=" ", flush=True)
            res = run_turn(q, skill_names)

            # Normalize: which position (0 or 1) was selected?
            sel_name = res["selected"]
            if sel_name:
                # find position in skill_defs
                pos = next(
                    (i for i, (n,_,_) in enumerate(skill_defs) if n in (sel_name or "")),
                    None
                )
                slot = f"pos{pos}" if pos is not None else "other"
            else:
                slot = "none"

            print(f"→ {sel_name or 'NONE'} ({slot})")
            if res["error"]:
                print(f"       ERR: {res['error']}")

            records.append({
                "condition": cid,
                "order": order_label,
                "query": q,
                "skill_names": skill_names,
                "selected": sel_name,
                "slot": slot,
                "paths": res["paths"],
                "error": res["error"],
            })
            time.sleep(2)

    cleanup()

    # Stats
    by_slot = Counter(r["slot"] for r in records)
    by_order = defaultdict(Counter)
    for r in records:
        by_order[r["order"]][r["slot"]] += 1

    return {
        "condition": cid,
        "desc": cond["desc"],
        "total": len(records),
        "by_slot": dict(by_slot),
        "by_order": {k: dict(v) for k, v in by_order.items()},
        "records": records,
    }


def print_analysis(summaries: list[dict]):
    print("\n" + "="*70)
    print("BIAS ANALYSIS")
    print("="*70)
    for s in summaries:
        cid = s["condition"]
        by_slot = s["by_slot"]
        total = s["total"]
        decided = total - by_slot.get("none", 0)

        p0 = by_slot.get("pos0", 0)
        p1 = by_slot.get("pos1", 0)
        pct0 = p0 / decided * 100 if decided else 0

        print(f"\n[{cid}] {s['desc']}")
        print(f"  pos0 wins: {p0:3d}/{decided}  ({pct0:.0f}%)")
        print(f"  pos1 wins: {p1:3d}/{decided}  ({100-pct0:.0f}%)")
        print(f"  undetected: {by_slot.get('none', 0)}")

        if pct0 > 65:
            finding = "⚠️  PRIMACY BIAS (first skill preferred)"
        elif pct0 < 35:
            finding = "⚠️  RECENCY BIAS (last skill preferred)"
        else:
            finding = "✅  No significant position bias"
        print(f"  → {finding}")

        if cid == "C2_length":
            # pos0 = long in A_first, short in B_first
            long_wins = (by_slot.get("pos0", 0)   # long is pos0 in A_first
                         if "A_first" in s["by_order"] else 0)
            # Correct accounting across orders
            long_w  = s["by_order"].get("A_first",{}).get("pos0",0) + \
                      s["by_order"].get("B_first",{}).get("pos1",0)
            short_w = s["by_order"].get("A_first",{}).get("pos1",0) + \
                      s["by_order"].get("B_first",{}).get("pos0",0)
            d2 = long_w + short_w
            if d2:
                print(f"  (length-adjusted) long wins: {long_w}/{d2} ({long_w/d2*100:.0f}%)")
                if long_w > short_w * 1.5:
                    print("  → ⚠️  LENGTH BIAS: longer desc preferred")
                elif short_w > long_w * 1.5:
                    print("  → ⚠️  REVERSE length bias: shorter preferred")
                else:
                    print("  → ✅  No length bias")

        if cid == "C5_name_desc_conflict":
            # pos0 in A_first = crystal_named (crystal name, github desc)
            # pos1 in A_first = github_named  (github name, crystal desc)
            # Crystal queries: desc_match = github_named (pos1 in A, pos0 in B)
            # Crystal queries: name_match = crystal_named (pos0 in A, pos1 in B)
            name_match = s["by_order"].get("A_first",{}).get("pos0",0) + \
                         s["by_order"].get("B_first",{}).get("pos1",0)
            desc_match = s["by_order"].get("A_first",{}).get("pos1",0) + \
                         s["by_order"].get("B_first",{}).get("pos0",0)
            d3 = name_match + desc_match
            if d3:
                print(f"  Name-match wins: {name_match}/{d3} ({name_match/d3*100:.0f}%)")
                print(f"  Desc-match wins: {desc_match}/{d3} ({desc_match/d3*100:.0f}%)")
                if name_match > desc_match * 1.5:
                    print("  → NAME dominates routing (A1 attack: craft matching name)")
                elif desc_match > name_match * 1.5:
                    print("  → DESCRIPTION dominates routing (A1 attack: craft matching desc)")
                else:
                    print("  → Name and description roughly co-equal")


def main():
    print("OpenClaw Real Agent Skill Selection Bias Probe v2")
    print(f"Workspace: {WORKSPACE}")
    print(f"Gateway log: {GATEWAY_LOG}")
    cleanup()

    all_summaries = []
    all_records   = []

    for cond in CONDITIONS:
        try:
            s = run_condition(cond)
            records = s.pop("records", [])
            all_summaries.append(s)
            all_records.extend(records)
        except KeyboardInterrupt:
            print("\nInterrupted. Saving partial results...")
            break
        except Exception as e:
            import traceback
            print(f"  [ERROR] {cond['id']}: {e}")
            traceback.print_exc()

    # Save
    recs_path = RESULTS_DIR / "bias_records.jsonl"
    recs_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in all_records)
    )
    summ_path = RESULTS_DIR / "bias_summary.json"
    summ_path.write_text(json.dumps(all_summaries, indent=2, ensure_ascii=False))

    print_analysis(all_summaries)
    print(f"\n✅ Done. Results: {RESULTS_DIR}/")

    # Print CSV for quick tabular view
    print("\nCSV:")
    print("condition,order,query,slot")
    for r in all_records:
        print(f"{r['condition']},{r['order']},{r['query'][:30]},{r['slot']}")


if __name__ == "__main__":
    main()
