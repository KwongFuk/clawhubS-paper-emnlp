#!/usr/bin/env python3
"""
quick_bias.py  ─ 15-min edition
================================
对应 BiasBusters (ICLR 2026) 的 4 个核心偏差，各做一个最小可信实验：

  B1  位置偏差   — 相同技能，顺序 A-B vs B-A，看 first-listed 胜率
  B2  描述长度   — 长描述 vs 短描述，控制顺序，看长度是否决定选择
  B3  描述碰撞   — 近似描述(via vs using)，看 position 是否打破平局
  B4  Name↔Desc — 名字指 ZQ9 但 desc 是 GitHub（反之亦然），看谁主导路由

参数: 4 queries × 2 orderings × 1 rep = 8 calls/条件 × 4 = 32 calls ≈ 20 min
"""
import json, os, subprocess, time
from collections import Counter
from pathlib import Path

OPENCLAW = "/opt/homebrew/bin/openclaw"
ENV      = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
WS       = "/Users/guangfu/codex/clawhubS"
OUTDIR   = Path(WS) / "skill-X/experiments/desc_importance/results_bias_probe"
OUTDIR.mkdir(exist_ok=True)

# ── Descriptions ──────────────────────────────────────────────────────────────
ZQ9_SHORT = "Answers questions about ZQ-9 crystal sensor calibration and resonance tuning."
ZQ9_LONG  = (
    "Answers all questions about ZQ-9 quantum crystal sensor calibration, resonance tuning, "
    "frequency sweep analysis, harmonic overtone detection, Q-factor measurement, "
    "temperature-compensated baseline correction, and automated calibration report generation. "
    "Compatible with ZQ-9A, ZQ-9B, and ZQ-9C variants. Outputs JSON, CSV, HDF5 reports."
)
GH_VIA    = "Manages GitHub repositories, creates issues, and reviews pull requests via the GitHub API."
GH_USING  = "Manages GitHub repositories, creates issues, and reviews pull requests using the GitHub API."

ZQ9_Q = [
    "Please calibrate the ZQ-9 crystal sensor",
    "I need ZQ-9 resonance tuning",
    "Run ZQ-9 frequency sweep",
    "ZQ-9 Q-factor baseline correction",
]
GH_Q = [
    "Create a GitHub issue for this bug",
    "List open pull requests in my repo",  
    "Review and merge the latest pull request",
    "Show recent commits on GitHub",
]

# ── Core LLM call ─────────────────────────────────────────────────────────────
ROUTER_PROMPT = """\
You are a skill router for an AI agent platform.
The following skills are available:

<available_skills>
{xml}
</available_skills>

Task: Given the user request below, output ONLY the name of the single best-matching skill.
Output exactly one skill name (no explanation, no punctuation). If none match, output NONE.

User request: {query}

Best skill:"""

def build_xml(skills):
    parts = []
    for name, desc in skills:
        parts.append(f'  <skill>\n    <name>{name}</name>\n    <description>{desc}</description>\n  </skill>')
    return "\n".join(parts)

def route(skills, query):
    xml = build_xml(skills)
    prompt = ROUTER_PROMPT.format(xml=xml, query=query)
    names = [s[0] for s in skills]
    for attempt in range(3):
        try:
            r = subprocess.run(
                [OPENCLAW, "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=90,
                env=ENV, cwd=WS
            )
            if r.returncode == 0 and r.stdout.strip():
                text = json.loads(r.stdout)["outputs"][0]["text"].strip().split()[0].strip(".,;:\"'")
                # exact match
                for n in names:
                    if n.lower() == text.lower():
                        return n
                # partial
                for n in names:
                    if text.lower() in n.lower() or n.lower() in text.lower():
                        return n
                return text  # raw response
        except Exception:
            if attempt < 2:
                time.sleep(3)
    return None

# ── Run one condition ─────────────────────────────────────────────────────────
def test_condition(tag, label, skills_A, skills_B, queries, expected_note=""):
    print(f"\n{'─'*58}")
    print(f"[{tag}] {label}")
    if expected_note:
        print(f"  Hypothesis: {expected_note}")

    results = {"A": {}, "B": {}}
    names_A = [s[0] for s in skills_A]
    names_B = [s[0] for s in skills_B]

    for q in queries:
        sel_A = route(skills_A, q)
        sel_B = route(skills_B, q)
        results["A"][q] = sel_A
        results["B"][q] = sel_B
        a_icon = "✓" if sel_A else "·"
        b_icon = "✓" if sel_B else "·"
        print(f"  {a_icon}A→{str(sel_A):22s}  {b_icon}B→{str(sel_B):22s}  | {q[:38]}")
        time.sleep(0.3)

    return {"tag": tag, "label": label, "A": results["A"], "B": results["B"],
            "names_A": names_A, "names_B": names_B}

# ── Analyze ───────────────────────────────────────────────────────────────────
def analyze(tag, label, A, B, names_A, names_B):
    """Return a verdict dict."""
    # Position-0 name in A = names_A[0]; position-0 name in B = names_B[0]
    pos0_wins, pos1_wins, total = 0, 0, 0
    for q in A:
        sel_a = A[q]
        sel_b = B[q]
        # In order A: names_A[0] is pos-0
        if sel_a == names_A[0]: pos0_wins += 1
        elif sel_a == names_A[1]: pos1_wins += 1
        # In order B: names_B[0] is pos-0
        if sel_b == names_B[0]: pos0_wins += 1
        elif sel_b == names_B[1]: pos1_wins += 1
        total += 2
    pct_pos0 = pos0_wins / total * 100 if total else 0
    return {
        "tag": tag, "label": label,
        "pos0_wins": pos0_wins, "pos1_wins": pos1_wins, "total": total,
        "pct_pos0": pct_pos0,
        "A_selections": A, "B_selections": B,
    }

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    t0 = time.time()
    print("Quick Bias Probe  —  openclaw infer model run")
    print("4 conditions × 4 queries × 2 orders = 32 LLM calls ≈ 20 min")
    print()

    all_results = []

    # ── B1: Position bias ────────────────────────────────────────────────────
    # Exact same skill, identical descriptions — only order differs
    r1 = test_condition(
        "B1_position",
        "位置偏差：相同技能，相同描述，顺序交换",
        skills_A=[("skill_alpha", ZQ9_SHORT), ("skill_beta",  ZQ9_SHORT)],
        skills_B=[("skill_beta",  ZQ9_SHORT), ("skill_alpha", ZQ9_SHORT)],
        queries=ZQ9_Q,
        expected_note="first-listed 胜率 > 60% → primacy bias 存在"
    )
    v1 = analyze("B1_position", r1["label"], r1["A"], r1["B"], r1["names_A"], r1["names_B"])
    all_results.append({**r1, "analysis": v1})

    # ── B2: Description length ────────────────────────────────────────────────
    # Long (8 sentences) vs Short (1 sentence), swap positions
    r2 = test_condition(
        "B2_length",
        "描述长度：长描述(8句) vs 短描述(1句)",
        skills_A=[("long_skill",  ZQ9_LONG),  ("short_skill", ZQ9_SHORT)],
        skills_B=[("short_skill", ZQ9_SHORT), ("long_skill",  ZQ9_LONG)],
        queries=ZQ9_Q,
        expected_note="长描述胜率不稳定（BiasBusters 发现：长度不是强预测因子）"
    )
    v2 = analyze("B2_length", r2["label"], r2["A"], r2["B"], r2["names_A"], r2["names_B"])
    # Also track: long_skill wins regardless of position
    long_wins  = sum(1 for q in r2["A"] if r2["A"][q]=="long_skill") + \
                 sum(1 for q in r2["B"] if r2["B"][q]=="long_skill")
    short_wins = sum(1 for q in r2["A"] if r2["A"][q]=="short_skill") + \
                 sum(1 for q in r2["B"] if r2["B"][q]=="short_skill")
    v2["long_wins"] = long_wins
    v2["short_wins"] = short_wins
    all_results.append({**r2, "analysis": v2})

    # ── B3: Description collision / perturbation ───────────────────────────────
    # "via" vs "using" — nearly identical, different name → position breaks tie?
    r3 = test_condition(
        "B3_desc_collision",
        "描述碰撞：近似描述 ('via' vs 'using')，不同名字",
        skills_A=[("gh_manager",    GH_VIA),   ("git_assistant", GH_USING)],
        skills_B=[("git_assistant", GH_USING), ("gh_manager",    GH_VIA)],
        queries=GH_Q,
        expected_note="BiasBusters: 描述扰动会改变选择 → 哪个技能赢取决于描述细节+位置"
    )
    v3 = analyze("B3_desc_collision", r3["label"], r3["A"], r3["B"], r3["names_A"], r3["names_B"])
    all_results.append({**r3, "analysis": v3})

    # ── B4: Name vs Description conflict ─────────────────────────────────────
    # gh_named has name~GitHub, desc=ZQ9   → desc matches ZQ9 queries
    # zq9_named has name~ZQ9,   desc=GitHub → name matches ZQ9 queries
    # Which wins for ZQ9 queries?
    r4 = test_condition(
        "B4_name_vs_desc",
        "Name↔Desc 冲突：名字指ZQ9但desc是GitHub，反之亦然",
        skills_A=[("gh_named",  ZQ9_SHORT), ("zq9_named", GH_VIA)],  
        # gh_named: name=gh but desc=ZQ9 → desc-match for ZQ9 query
        # zq9_named: name=zq9 but desc=GH → name-match for ZQ9 query
        skills_B=[("zq9_named", GH_VIA),   ("gh_named",  ZQ9_SHORT)],
        queries=ZQ9_Q,
        expected_note="BiasBusters: 描述语义相似度 > 名字匹配 → gh_named 应赢"
    )
    v4 = analyze("B4_name_vs_desc", r4["label"], r4["A"], r4["B"], r4["names_A"], r4["names_B"])
    # Extra: desc-match vs name-match wins
    desc_match = sum(1 for q in r4["A"] if r4["A"][q]=="gh_named") + \
                 sum(1 for q in r4["B"] if r4["B"][q]=="gh_named")
    name_match = sum(1 for q in r4["A"] if r4["A"][q]=="zq9_named") + \
                 sum(1 for q in r4["B"] if r4["B"][q]=="zq9_named")
    v4["desc_match_wins"] = desc_match
    v4["name_match_wins"] = name_match
    all_results.append({**r4, "analysis": v4})

    elapsed = time.time() - t0

    # ── Save ─────────────────────────────────────────────────────────────────
    out = OUTDIR / "quick_bias_results.json"
    out.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))

    # ── Print Summary ─────────────────────────────────────────────────────────
    print(f"\n{'='*58}")
    print(f"RESULTS  (elapsed {elapsed/60:.1f} min)")
    print(f"{'='*58}")

    for item in all_results:
        v = item["analysis"]
        tag = v["tag"]
        pct = v["pct_pos0"]
        tot = v["total"]
        p0  = v["pos0_wins"]

        print(f"\n[{tag}] {v['label']}")
        print(f"  Position-0 wins: {p0}/{tot}  ({pct:.0f}%)")

        if tag == "B1_position":
            verdict = ("⚠️  PRIMACY BIAS 存在 (>60%)" if pct > 60 else
                       "⚠️  RECENCY BIAS 存在 (<40%)" if pct < 40 else
                       "✅  无位置偏差 (≈50%)")
            print(f"  结论: {verdict}")

        elif tag == "B2_length":
            lw = v["long_wins"]; sw = v["short_wins"]
            lt = lw + sw
            lpct = lw/lt*100 if lt else 0
            verdict = ("⚠️  长度偏差: 长描述胜" if lpct > 65 else
                       "⚠️  短描述胜 (反向)" if lpct < 35 else
                       "✅  无长度偏差 (符合 BiasBusters 结论)")
            print(f"  long_skill wins: {lw}/{lt} ({lpct:.0f}%)")
            print(f"  position-0 wins: {p0}/{tot} ({pct:.0f}%)")
            print(f"  结论: {verdict}")

        elif tag == "B3_desc_collision":
            print(f"  A排序: { {q: item['A'][q] for q in item['A']} }")
            print(f"  B排序: { {q: item['B'][q] for q in item['B']} }")
            verdict = ("⚠️  位置打破平局 (primacy)" if pct > 60 else
                       "⚠️  位置打破平局 (recency)" if pct < 40 else
                       "✅  近似描述→随机选 (无稳定优先)")
            print(f"  结论: {verdict}")

        elif tag == "B4_name_vs_desc":
            dm = v["desc_match_wins"]; nm = v["name_match_wins"]
            dt = dm + nm
            dpct = dm/dt*100 if dt else 0
            print(f"  desc-match (gh_named)  wins: {dm}/{dt} ({dpct:.0f}%)")
            print(f"  name-match (zq9_named) wins: {nm}/{dt} ({100-dpct:.0f}%)")
            verdict = ("✅  DESC 主导路由 (符合 BiasBusters)" if dpct > 60 else
                       "⚠️  NAME 主导路由 (与 BiasBusters 矛盾)" if dpct < 40 else
                       "⚖️  Name 和 Desc 势均力敌")
            print(f"  结论: {verdict}")

    print(f"\n✅ Results → {out}")

if __name__ == "__main__":
    main()
