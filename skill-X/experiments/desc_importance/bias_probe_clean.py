#!/usr/bin/env python3
"""
bias_probe_clean.py
===================
在独立 micro-workspace 里测试 OpenClaw skill 选择偏差：
  C1: 位置偏差  — 相同技能，通过目录名字母序控制先后
  C2: 描述长度  — 同主题，长描述 vs 短描述
  C3: 描述碰撞  — 近似描述，不同名字
  C4: 名字碰撞  — 相似名字，相同描述
  C5: Name vs Desc 冲突 — 名字指向A任务，描述指向B任务

方法:
  - /tmp/bias_ws/ 作为独立 workspace（只装探测技能）
  - openclaw agents add bias-micro --workspace /tmp/bias_ws
  - 每次 turn 前清空 session 文件 → 保证 0 历史
  - 从 session jsonl 解析 tool_use read() 调用判断哪个技能被选中
"""


import json, os, re, shutil, subprocess, time
from pathlib import Path
from collections import Counter, defaultdict
from typing import Optional

try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    _SIM_AVAILABLE = True
except ImportError:
    _SIM_AVAILABLE = False
    print("[warn] sentence-transformers not installed — similarity audit disabled.")
    print("       pip install sentence-transformers")

# ─── Config ──────────────────────────────────────────────────────────────────
OPENCLAW    = "/opt/homebrew/bin/openclaw"
MICRO_WS    = Path("/tmp/bias_ws")
AGENT_ID    = "bias-micro"
N_REPS      = 3        # 每个 query 重复几次（LLM 非确定性）
RESULTS_DIR = Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/desc_importance/results_bias_probe")
RESULTS_DIR.mkdir(exist_ok=True)

ENV = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}

# ─── Workspace setup ─────────────────────────────────────────────────────────
def reset_workspace():
    """删掉旧 micro workspace，重建干净版本"""
    if MICRO_WS.exists():
        shutil.rmtree(MICRO_WS)
    MICRO_WS.mkdir(parents=True)
    (MICRO_WS / ".agents" / "skills").mkdir(parents=True)
    print(f"[setup] workspace reset: {MICRO_WS}")


def install_skill(name: str, description: str, body: str = ""):
    """在 micro workspace 里写一个 SKILL.md"""
    d = MICRO_WS / ".agents" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    if not body:
        body = f"When asked to handle this task, report: TRIGGERED_{name.upper()}"
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n"
    )


def ensure_agent():
    """确保 bias-micro agent 存在且指向 micro workspace"""
    # Check if already exists
    r = subprocess.run([OPENCLAW, "agents", "list"], capture_output=True, text=True, env=ENV)
    if AGENT_ID in r.stdout:
        # Delete and recreate to point to fresh workspace
        subprocess.run([OPENCLAW, "agents", "delete", AGENT_ID, "--yes"],
                       capture_output=True, env=ENV)

    r = subprocess.run(
        [OPENCLAW, "agents", "add", AGENT_ID,
         "--workspace", str(MICRO_WS),
         "--non-interactive", "--json"],
        capture_output=True, text=True, env=ENV
    )
    if r.returncode != 0:
        print(f"[WARN] agents add failed: {r.stderr[:300]}")
        # May not have --yes flag — try without
        r2 = subprocess.run(
            [OPENCLAW, "agents", "add", AGENT_ID,
             "--workspace", str(MICRO_WS),
             "--non-interactive"],
            capture_output=True, text=True, env=ENV
        )
        print(r2.stdout[:200], r2.stderr[:200])
    else:
        info = {}
        try: info = json.loads(r.stdout.split('\n')[-2] or r.stdout)
        except: pass
        print(f"[setup] agent '{AGENT_ID}' created → {info.get('agentDir','?')}")


def get_session_file() -> Path | None:
    """Find the session jsonl file for bias-micro agent"""
    agent_dir = Path.home() / ".openclaw" / "agents" / AGENT_ID
    sess_dir = agent_dir / "sessions"
    files = sorted(sess_dir.glob("*.jsonl")) if sess_dir.exists() else []
    # Filter out .checkpoint files
    files = [f for f in files if "checkpoint" not in f.name]
    return files[0] if files else None


def clear_session():
    """Truncate session file to force empty history on next run"""
    sf = get_session_file()
    if sf and sf.exists():
        sf.write_text("")  # empty → openclaw treats as new session
    # Also clear sessions.json skill snapshot cache
    sj = Path.home() / ".openclaw" / "agents" / AGENT_ID / "sessions" / "sessions.json"
    if sj.exists():
        sj.write_text("{}")


def verify_normal_mode() -> bool:
    """Check that total description chars is under compact threshold"""
    total = 0
    for md in (MICRO_WS / ".agents" / "skills").rglob("SKILL.md"):
        content = md.read_text()
        m = re.search(r"description:\s*(.+)", content)
        if m:
            total += len(m.group(1).strip())
    print(f"[verify] Total desc chars: {total} (threshold=30000, compact={'YES' if total>30000 else 'NO'})")
    return total < 30000

# ─── Run one agent turn ───────────────────────────────────────────────────────
def run_turn(query: str) -> dict:
    """
    Run one agent turn (fresh history), parse session file for read() calls.
    Returns {selected: name|None, paths: [...], reply: str}
    """
    clear_session()

    try:
        r = subprocess.run(
            [OPENCLAW, "agent", "--agent", AGENT_ID, "--local",
             "--message", query, "--verbose", "on"],
            capture_output=True, text=True, timeout=90,
            cwd=str(MICRO_WS), env=ENV
        )
        combined = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return {"selected": None, "paths": [], "reply": "TIMEOUT"}

    # Extract agent reply (non-diagnostic last lines)
    reply_lines = [
        l for l in combined.splitlines()
        if not l.startswith(("[", "Config", "bundle", "web_", "Registered", "🦞", "LLM"))
        and l.strip()
    ]
    reply = "\n".join(reply_lines[-3:])

    # Parse session jsonl for tool_use "read" calls
    sf = get_session_file()
    paths = []
    if sf and sf.exists():
        for line in sf.read_text().splitlines():
            try:
                entry = json.loads(line)
                msg = entry.get("message", {})
                for blk in (msg.get("content") or []):
                    if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("name") == "read":
                        p = (blk.get("input") or {}).get("path", "")
                        if p:
                            paths.append(p)
            except:
                pass

    # Fallback: grep combined output for SKILL.md paths
    if not paths:
        paths = re.findall(r"[\w/_-]+/SKILL\.md", combined)

    # Map paths → skill name (directory name in our workspace)
    selected = None
    skill_names = [d.name for d in (MICRO_WS / ".agents" / "skills").iterdir() if d.is_dir()]
    for path in paths:
        for name in skill_names:
            if name in path:
                selected = name
                break
        if selected:
            break

    return {"selected": selected, "paths": paths, "reply": reply}


# ─── Experiment runner ────────────────────────────────────────────────────────
def run_experiment(
    exp_id: str,
    exp_desc: str,
    skill_pairs: list[tuple],   # [(name_a, desc_a), (name_b, desc_b)]  A-first order
    skill_pairs_b: list[tuple], # B-first order
    queries: list[str],
) -> dict:
    """Run one experiment condition. skill_pairs = [(name,desc), ...]"""
    print(f"\n{'='*60}")
    print(f"[{exp_id}] {exp_desc}")

    all_records = []

    for order_label, pairs in [("A_first", skill_pairs), ("B_first", skill_pairs_b)]:
        # Rebuild workspace with this ordering
        reset_workspace()
        for name, desc in pairs:
            install_skill(name, desc)
        if not verify_normal_mode():
            print("[WARN] Compact mode triggered! Add fewer/shorter skills.")

        # One trigger run to build session + skill snapshot
        # (first run always initializes session file)
        print(f"\n  [{order_label}] skills: {[p[0] for p in pairs]}")
        # Warm-up call to let openclaw create the session file and skill snapshot
        warmup = run_turn("hello, just checking in")
        time.sleep(1)

        for q in queries:
            sel_list = []
            for rep in range(N_REPS):
                res = run_turn(q)
                sel = res["selected"]
                sel_list.append(sel)
                icon = "🎯" if sel else "❓"
                print(f"    {icon} rep{rep} [{order_label}] → {sel or 'NONE'}  | {q[:45]}")
                time.sleep(1)

            # Majority vote for this query
            majority = Counter(sel_list).most_common(1)[0][0]
            all_records.append({
                "exp": exp_id,
                "order": order_label,
                "query": q,
                "reps": sel_list,
                "majority": majority,
                "skills_order": [p[0] for p in pairs],
            })

    # Stats
    by_order = defaultdict(Counter)
    for r in all_records:
        by_order[r["order"]][r["majority"]] += 1

    return {
        "exp_id": exp_id,
        "exp_desc": exp_desc,
        "records": all_records,
        "by_order": {k: dict(v) for k, v in by_order.items()},
    }


# ─── Semantic Similarity Auditor ─────────────────────────────────────────────
_SIM_MODEL: Optional["SentenceTransformer"] = None  # lazy-loaded

SIM_THRESHOLDS = {
    "high":   0.60,   # cosine ≥ 0.60  → strong semantic match (empirical: ~0.80)
    "medium": 0.25,   # 0.25 ≤ cosine < 0.60  (empirical: ~0.34)
    "low":    0.02,   # 0.02 ≤ cosine < 0.25  (empirical: ~0.03)
    "noise":  0.0,    # cosine < 0.02  → essentially unrelated (empirical: ~0.004)
}

def _sim_model() -> Optional["SentenceTransformer"]:
    global _SIM_MODEL
    if not _SIM_AVAILABLE:
        return None
    if _SIM_MODEL is None:
        print("[sim] loading all-MiniLM-L6-v2 ...")
        _SIM_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _SIM_MODEL


def cosine_sim(a: "np.ndarray", b: "np.ndarray") -> float:
    """Cosine similarity between two 1-D vectors."""
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


def sim_tier(score: float) -> str:
    """Map a cosine score to a named tier."""
    if score >= SIM_THRESHOLDS["high"]:
        return "high"
    if score >= SIM_THRESHOLDS["medium"]:
        return "medium"
    if score >= SIM_THRESHOLDS["low"]:
        return "low"
    return "noise"


def audit_similarity(
    query: str,
    skill_descs: dict[str, str],  # {name: description}
) -> dict[str, dict]:
    """
    Compute cosine similarity between a query and each skill description.

    Returns:
        {skill_name: {"score": float, "tier": str}}
    """
    model = _sim_model()
    if model is None:
        return {name: {"score": None, "tier": "unknown"} for name in skill_descs}

    texts = [query] + list(skill_descs.values())
    embs  = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    q_emb = embs[0]
    result = {}
    for i, name in enumerate(skill_descs):
        sc = cosine_sim(q_emb, embs[i + 1])
        result[name] = {"score": round(sc, 4), "tier": sim_tier(sc)}
    return result


def build_desc_for_tier(tier: str, topic: str = "ZQ-9 crystal sensor") -> str:
    """
    Return a description text crafted to fall in the given similarity tier
    relative to ZQ9_QUERIES.

    Calibration (MiniLM-L6-v2 cosine vs. ZQ9_QUERIES):
      high   ≈ 0.81 — same domain + product vocabulary
      medium ≈ 0.34 — adjacent hardware domain, different terminology
      low    ≈ 0.03 — industrial but domain-orthogonal
      noise  ≈ 0.00 — completely unrelated domain
    """
    templates = {
        "high": (
            f"Calibrates {topic} sensors, performs resonance tuning, frequency sweep "
            "analysis, and Q-factor measurement. Outputs calibration reports."
        ),
        "medium": (
            "Runs hardware device diagnostics, validates electronic signal quality, "
            "and performs baseline measurement corrections for lab instruments."
        ),
        "low": (
            "Manages software deployment pipelines, monitors CI/CD build status, "
            "and tracks code repository branches for development teams."
        ),
        "noise": (
            "Books restaurant reservations, curates wine pairing suggestions, "
            "and generates seasonal menu recommendations for dining experiences."
        ),
    }
    return templates[tier]


# ─── C6: Semantic Threshold Experiment ───────────────────────────────────────

def run_c6_threshold_experiment() -> dict:
    """
    C6: Semantic Similarity Threshold Bias

    Research question:
      Does the agent respect semantic similarity when routing?
      If a high-sim skill and a low-sim skill co-exist, does it
      always route to the high-sim one — or does position/name
      override the similarity signal?

    Design:
      For each of 4 similarity tiers (high / medium / low / noise)
      we install TWO skills:
        - S_target : description at the target tier
        - S_high   : always a HIGH-sim skill (ground-truth anchor)

      We run ZQ9_QUERIES 3× each and record:
        a) which skill was selected
        b) the cosine score of each skill vs the query
        c) whether the agent chose the higher-sim skill ("correct" choice)

    Thresholds tested (cosine):
      0.20, 0.30, 0.45, 0.55, 0.70
    """
    exp_id  = "C6_sim_threshold"
    exp_desc = "语义相似度阈值偏置：agent 路由是否遵循余弦相似度信号"
    print(f"\n{'='*60}")
    print(f"[{exp_id}] {exp_desc}")

    if not _SIM_AVAILABLE:
        print("  ⚠️  SKIPPED — sentence-transformers not available")
        return {"exp_id": exp_id, "exp_desc": exp_desc, "skipped": True, "records": []}

    tiers       = ["high", "medium", "low", "noise"]
    all_records = []

    for tier in tiers:
        target_desc = build_desc_for_tier(tier)
        anchor_desc = build_desc_for_tier("high")
        skill_anchor = "anchor_high_zq9"
        skill_target = f"target_{tier}_zq9"

        # Pre-compute expected sim scores
        sim_q0   = ZQ9_QUERIES[0]
        precheck = audit_similarity(sim_q0, {
            skill_anchor: anchor_desc,
            skill_target: target_desc,
        })
        print(f"\n  [tier={tier}] expected cosine:")
        for name, info in precheck.items():
            print(f"    {name}: {info['score']:.3f} ({info['tier']})")

        for order_label, pairs in [
            ("anchor_first", [(skill_anchor, anchor_desc), (skill_target, target_desc)]),
            ("target_first", [(skill_target, target_desc), (skill_anchor, anchor_desc)]),
        ]:
            reset_workspace()
            for name, desc in pairs:
                install_skill(name, desc)
            verify_normal_mode()

            warmup = run_turn("hello, just initializing")
            time.sleep(1)

            for q in ZQ9_QUERIES:
                sim_info = audit_similarity(q, {
                    skill_anchor: anchor_desc,
                    skill_target: target_desc,
                })
                for rep in range(N_REPS):
                    res = run_turn(q)
                    sel = res["selected"]
                    anchor_score = sim_info[skill_anchor]["score"]
                    target_score = sim_info[skill_target]["score"]

                    # "correct" = chose the higher-sim skill
                    if sel == skill_anchor:
                        correct = anchor_score >= target_score
                    elif sel == skill_target:
                        correct = target_score >= anchor_score
                    else:
                        correct = None  # no selection

                    gap = round(anchor_score - target_score, 4) if (
                        anchor_score is not None and target_score is not None
                    ) else None

                    record = {
                        "exp": exp_id,
                        "tier": tier,
                        "order": order_label,
                        "query": q,
                        "rep": rep,
                        "selected": sel,
                        "sim_anchor": anchor_score,
                        "sim_target": target_score,
                        "sim_gap": gap,
                        "correct_choice": correct,
                    }
                    all_records.append(record)

                    icon = ("✅" if correct else ("❌" if correct is False else "❓"))
                    print(
                        f"    {icon} [{order_label}][{tier}] rep{rep} "
                        f"→ {sel or 'NONE':25s} "
                        f"| anchor={anchor_score:.3f} target={target_score:.3f} "
                        f"gap={gap:+.3f}"
                    )
                    time.sleep(1)

    # ── Threshold analysis ──────────────────────────────────────────────────
    _print_c6_analysis(all_records)

    return {
        "exp_id": exp_id,
        "exp_desc": exp_desc,
        "records": all_records,
        "thresholds": SIM_THRESHOLDS,
    }


def _print_c6_analysis(records: list[dict]):
    """Print per-tier routing accuracy and threshold crossing summary."""
    print("\n" + "─" * 60)
    print("C6 Threshold Analysis")
    print("─" * 60)
    print(f"{'Tier':<10} {'N':>4} {'Correct%':>10} {'Mean gap':>10} {'Agent ignores gap?'}")
    print("─" * 60)

    for tier in ["high", "medium", "low", "noise"]:
        tier_recs = [r for r in records if r["tier"] == tier and r["correct_choice"] is not None]
        if not tier_recs:
            continue
        n       = len(tier_recs)
        correct = sum(1 for r in tier_recs if r["correct_choice"])
        pct     = correct / n * 100
        gaps    = [r["sim_gap"] for r in tier_recs if r["sim_gap"] is not None]
        mean_gap = sum(gaps) / len(gaps) if gaps else 0.0

        # "ignores gap" = agent chose low-sim skill when gap > 0.20
        large_gap_recs = [r for r in tier_recs if r["sim_gap"] is not None and r["sim_gap"] > 0.20]
        wrong_large    = sum(1 for r in large_gap_recs if not r["correct_choice"])
        bias_flag = f"⚠️  {wrong_large}/{len(large_gap_recs)} mistakes at gap>0.20" if wrong_large else "✅ consistent"

        print(f"{tier:<10} {n:>4} {pct:>9.1f}% {mean_gap:>+10.3f} {bias_flag}")

    # Threshold crossing: at which sim_gap does correctness drop below 80%?
    thresholds = [0.10, 0.20, 0.30, 0.40, 0.50]
    print("\nRouting correctness by sim_gap bucket:")
    print(f"  {'gap ≥':>8}  {'correct%':>10}  {'n':>4}")
    for thr in thresholds:
        bucket = [r for r in records if r["sim_gap"] is not None and r["sim_gap"] >= thr
                  and r["correct_choice"] is not None]
        if not bucket:
            continue
        pct = sum(1 for r in bucket if r["correct_choice"]) / len(bucket) * 100
        print(f"  {thr:>8.2f}  {pct:>9.1f}%  {len(bucket):>4}")

    print(
        "\nInterpretation: If correctness stays ~100% across all gap buckets, "
        "the agent correctly uses description semantics for routing.\n"
        "Drops below 80% at a gap threshold indicate routing ignores the "
        "semantic signal — position or name dominates instead."
    )

SHORT = "Answers questions about ZQ-9 crystal sensor calibration and resonance tuning."
LONG  = (
    "Answers all questions about ZQ-9 quantum crystal sensor calibration, resonance tuning, "
    "frequency sweep analysis, harmonic overtone detection, Q-factor measurement, "
    "temperature compensation, baseline correction, and calibration report generation. "
    "Compatible with ZQ-9A, ZQ-9B, ZQ-9C variants. Outputs JSON, CSV, HDF5 format reports."
)
DESC_A = "Manages GitHub repositories, creates issues, reviews pull requests via GitHub API."
DESC_B = "Manages GitHub repositories, creates issues, reviews pull requests using GitHub API."

ZQ9_QUERIES = [
    "Please calibrate my ZQ-9 crystal sensor",
    "I need ZQ-9 resonance tuning help",
    "Can you do a ZQ-9 frequency sweep?",
    "Run ZQ-9 calibration now",
    "Help with ZQ-9 sensor setup",
]
GH_QUERIES = [
    "Create a new GitHub issue for this bug",
    "List all open pull requests in my repo",
    "Review the latest pull request",
    "Merge the current pull request",
    "Check recent GitHub commits",
]


def main():
    print("OpenClaw Bias Probe — Clean Micro-Workspace Experiment")
    print(f"Workspace: {MICRO_WS}")
    print(f"Agent: {AGENT_ID}")

    # === Setup agent (once) ===
    reset_workspace()
    ensure_agent()

    results = []

    # ─── C1: Position Bias (identical skills, swap alphabetical order) ─────────
    # a0_zq9 comes before b1_zq9 alphabetically → a0 is "first"
    r = run_experiment(
        "C1_position",
        "位置偏差：完全相同技能，仅字母序前后不同",
        skill_pairs   = [("a0_zq9_skill", SHORT), ("b1_zq9_skill", SHORT)],
        skill_pairs_b = [("a0_zq9_skill", SHORT), ("b1_zq9_skill", SHORT)],  # same (position fixed by alpha-sort)
        queries=ZQ9_QUERIES,
    )
    results.append(r)

    # Actually for C1 we need to control order via different approach:
    # Since openclaw scans alphabetically, swapping means using different names
    r2 = run_experiment(
        "C1b_position_swap",
        "位置偏差B：通过名字前缀 0_ vs 1_ 控制顺序",
        skill_pairs   = [("0_zq9_alpha", SHORT), ("1_zq9_beta",  SHORT)],  # alpha=pos0
        skill_pairs_b = [("0_zq9_beta",  SHORT), ("1_zq9_alpha", SHORT)],  # beta=pos0
        queries=ZQ9_QUERIES,
    )
    results.append(r2)

    # ─── C2: Length Bias ──────────────────────────────────────────────────────
    r = run_experiment(
        "C2_length",
        "描述长度偏差：同主题，长描述(8句) vs 短描述(1句)",
        skill_pairs   = [("0_zq9_long",  LONG),  ("1_zq9_short", SHORT)],
        skill_pairs_b = [("0_zq9_short", SHORT), ("1_zq9_long",  LONG)],
        queries=ZQ9_QUERIES,
    )
    results.append(r)

    # ─── C3: Description Collision ───────────────────────────────────────────
    # Near-identical descriptions, different names
    r = run_experiment(
        "C3_desc_collision",
        "描述碰撞：近似描述(via vs using)，不同名字",
        skill_pairs   = [("github_manager", DESC_A), ("git_assistant",  DESC_B)],
        skill_pairs_b = [("git_assistant",  DESC_B), ("github_manager", DESC_A)],
        queries=GH_QUERIES,
    )
    results.append(r)

    # ─── C4: Name Collision ───────────────────────────────────────────────────
    r = run_experiment(
        "C4_name_collision",
        "名字碰撞：相似名字(assistant vs helper)，相同描述",
        skill_pairs   = [("zq9_assistant", SHORT), ("zq9_helper", SHORT)],
        skill_pairs_b = [("zq9_helper",    SHORT), ("zq9_assistant", SHORT)],
        queries=ZQ9_QUERIES,
    )
    results.append(r)

    # ─── C5: Name vs Desc Conflict ────────────────────────────────────────────
    # github_named has name→github but desc→zq9; zq9_named has name→zq9 but desc→github
    # For ZQ9 queries: desc_match = github_named, name_match = zq9_named
    r = run_experiment(
        "C5_name_vs_desc",
        "Name vs Desc 冲突：名字指向A，描述指向B",
        skill_pairs   = [("github_named", SHORT), ("zq9_named", DESC_A)],  # github_name+zq9_desc vs zq9_name+github_desc
        skill_pairs_b = [("zq9_named",    DESC_A), ("github_named", SHORT)],
        queries=ZQ9_QUERIES,  # ZQ9 queries → desc-match = github_named; name-match = zq9_named
    )
    results.append(r)

    # ─── C6: Semantic Similarity Threshold Bias ─────────────────────────────────
    r_c6 = run_c6_threshold_experiment()
    results.append(r_c6)

    # ─── Save all results ─────────────────────────────────────────────────
    out = RESULTS_DIR / "bias_clean_results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\n✅ Results saved to {out}")

    # Save C6 separately for easy analysis
    c6_out = RESULTS_DIR / "c6_sim_threshold_results.json"
    c6_out.write_text(json.dumps(r_c6, indent=2, ensure_ascii=False))
    print(f"✅ C6 results saved to {c6_out}")


    # ─── Print summary ────────────────────────────────────────────────────────
    print("\n" + "="*70)
    print("BIAS ANALYSIS SUMMARY")
    print("="*70)

    for r in results:
        if "by_order" not in r:
            continue  # C6 uses different schema; already printed by _print_c6_analysis
        eid = r["exp_id"]
        by_order = r["by_order"]
        print(f"\n[{eid}] {r['exp_desc']}")

        # Count position-0 vs position-1 wins across both orders
        # In A_first: first skill alphabetically = position 0
        # In B_first: different skill = position 0
        all_selections = []
        for order, counts in by_order.items():
            for sel, n in counts.items():
                all_selections.extend([(sel, order)] * n)

        print(f"  Selection counts (majority vote per query):")
        for order, counts in by_order.items():
            print(f"  {order}: {counts}")

        # Position analysis
        a_first_counts = by_order.get("A_first", {})
        b_first_counts = by_order.get("B_first", {})

        if eid == "C1b_position_swap":
            # alpha is pos0 in A_first, beta is pos0 in B_first
            pos0_wins = a_first_counts.get("0_zq9_alpha", 0) + b_first_counts.get("0_zq9_beta", 0)
            pos1_wins = a_first_counts.get("1_zq9_beta",  0) + b_first_counts.get("1_zq9_alpha", 0)
            total = pos0_wins + pos1_wins
            if total:
                pct = pos0_wins / total * 100
                print(f"  Position-0 wins: {pos0_wins}/{total} ({pct:.0f}%)")
                print(f"  → {'⚠️  PRIMACY BIAS' if pct>65 else '⚠️  RECENCY BIAS' if pct<35 else '✅  No position bias'}")

        elif eid == "C2_length":
            # long is pos0 in A_first, short is pos0 in B_first
            long_wins  = a_first_counts.get("0_zq9_long",  0) + b_first_counts.get("1_zq9_long",  0)
            short_wins = a_first_counts.get("1_zq9_short", 0) + b_first_counts.get("0_zq9_short", 0)
            total = long_wins + short_wins
            if total:
                pct = long_wins / total * 100
                print(f"  Long-desc wins: {long_wins}/{total} ({pct:.0f}%)")
                print(f"  → {'⚠️  LENGTH BIAS (longer preferred)' if pct>65 else '⚠️  Shorter preferred' if pct<35 else '✅  No length bias'}")

        elif eid == "C5_name_vs_desc":
            # For ZQ9 queries:
            # name-match = zq9_named (name=zq9, desc=github_desc = DESC_A)
            # desc-match = github_named (name=github, desc=SHORT = zq9 desc)
            name_wins = (a_first_counts.get("zq9_named",    0) +
                         b_first_counts.get("zq9_named",    0))
            desc_wins = (a_first_counts.get("github_named", 0) +
                         b_first_counts.get("github_named", 0))
            total = name_wins + desc_wins
            if total:
                print(f"  Name-match (zq9_named) wins: {name_wins}/{total} ({name_wins/total*100:.0f}%)")
                print(f"  Desc-match (github_named) wins: {desc_wins}/{total} ({desc_wins/total*100:.0f}%)")
                if name_wins > desc_wins:
                    print("  → NAME dominates routing (A1 attack vector: craft matching name)")
                elif desc_wins > name_wins:
                    print("  → DESCRIPTION dominates routing (A1 attack vector: craft matching desc)")
                else:
                    print("  → Name and description roughly tied")


if __name__ == "__main__":
    main()
