#!/usr/bin/env python3
"""
run_c6_only.py
==============
C6: Semantic Similarity Threshold Bias

Uses the SAME working setup as run_bias_experiment.py:
  - Profile:    --profile bias2
  - Agent:      experiment
  - Workspace:  skill-X/experiments/bias_probe_ws/

Research question:
  Does OpenClaw agent routing track cosine similarity?
  When two skills have descriptions at different similarity tiers,
  does the agent always pick the higher-sim one — or does
  position/name override the semantic signal?

Design:
  For each tier in {high, medium, low, noise}:
    Install 2 skills:
      anchor_high_zq9  : always HIGH cosine similarity (≈ 0.81)
      target_{tier}    : similarity at the target tier
    Run ZQ9_QUERIES x N_REPS in both orderings.
    Record:
      - which skill was selected
      - cosine sim of each skill vs the query
      - whether the higher-sim skill was chosen (correct_choice)

Empirical cosine calibration (MiniLM-L6-v2 vs ZQ9 queries):
  high   ≈ 0.81  — same product vocabulary
  medium ≈ 0.34  — adjacent hardware, different terms
  low    ≈ 0.03  — software domain (CI/CD)
  noise  ≈ 0.004 — restaurant/hospitality

Threshold used for "significant gap": cosine_gap > 0.20
"""

import json, os, re, subprocess, time, pathlib, shutil
from collections import Counter, defaultdict
from typing import Optional

# ── sentence-transformers (optional but recommended) ──────────────────────────
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    _SIM_OK = True
except ImportError:
    _SIM_OK = False
    print("[warn] sentence-transformers not installed — sim scores will be None")

# ── Config (mirror run_bias_experiment.py) ────────────────────────────────────
PATH_ENV = "/opt/homebrew/bin:" + os.environ.get("PATH", "")
ENV      = {**os.environ, "PATH": PATH_ENV}
OPENCLAW = "/opt/homebrew/bin/openclaw"
PROFILE  = ""   # empty = use default ~/.openclaw profile (verified working)
AGENT    = "c6exp"
EXP_WS   = pathlib.Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/bias_probe_ws")
SESS_DIR = pathlib.Path.home() / ".openclaw/agents/c6exp/sessions"
RESULTS  = pathlib.Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/desc_importance/results_bias_probe")
RESULTS.mkdir(exist_ok=True)

SKILLS  = EXP_WS / ".agents" / "skills"
N_REPS  = 3   # repetitions per (query, order) pair

ZQ9_QUERIES = [
    "Please calibrate the ZQ-9 crystal sensor",
    "I need ZQ-9 resonance tuning help",
    "Run a ZQ-9 frequency sweep calibration",
    "ZQ-9 sensor setup and calibration",
    "Help with ZQ-9 baseline correction",
]

# Descriptions calibrated to each similarity tier
TIER_DESCS = {
    "high": (
        "Calibrates ZQ-9 crystal sensor, performs resonance tuning, frequency sweep "
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

# ── Skill management ──────────────────────────────────────────────────────────
def clear_skills():
    if SKILLS.exists():
        shutil.rmtree(SKILLS)
    SKILLS.mkdir(parents=True)

def install(name: str, desc: str):
    d = SKILLS / name
    d.mkdir(parents=True, exist_ok=True)
    trigger = f"TRIGGERED_{name.upper().replace('-', '_')}"
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n{trigger}\n"
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
    sj = SESS_DIR / "sessions.json"
    if sj.exists():
        sj.write_text("{}")
    sf = get_session_file()
    if sf:
        sf.write_text("")

def parse_selected(content: str, skill_names: list[str]) -> str | None:
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

# ── One agent turn ────────────────────────────────────────────────────────────
def run_turn(query: str, skill_names: list[str]) -> dict:
    clear_session()
    cmd = [OPENCLAW]
    # No --profile flag = use default ~/.openclaw
    cmd += ["agent", "--agent", AGENT, "--local",
            "--message", query, "--verbose", "on"]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=90,
            cwd=str(EXP_WS), env=ENV
        )
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return {"selected": None, "error": "TIMEOUT"}

    # Try session jsonl first
    sf = get_session_file()
    selected = None
    if sf and sf.exists():
        selected = parse_selected(sf.read_text(), skill_names)

    # Fallback: TRIGGERED_ marker in stdout
    if not selected:
        for name in skill_names:
            if f"TRIGGERED_{name.upper().replace('-','_')}" in out:
                selected = name
                break

    # Fallback: skill name in output lines
    if not selected:
        for line in out.splitlines():
            for name in skill_names:
                if name in line:
                    selected = name
                    break
            if selected:
                break

    return {"selected": selected, "rc": r.returncode}

# ── Cosine similarity ─────────────────────────────────────────────────────────
_model: Optional["SentenceTransformer"] = None

def get_sim(query: str, descs: dict[str, str]) -> dict[str, float | None]:
    """Return {name: cosine_score} for each description."""
    if not _SIM_OK:
        return {k: None for k in descs}
    global _model
    if _model is None:
        print("[sim] loading all-MiniLM-L6-v2...")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    texts = [query] + list(descs.values())
    embs  = _model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    q = embs[0]
    return {
        name: round(float(np.dot(q, embs[i + 1])), 4)
        for i, name in enumerate(descs)
    }

# ── C6 experiment ─────────────────────────────────────────────────────────────
def run_c6() -> dict:
    print("\n" + "=" * 60)
    print("C6: Semantic Similarity Threshold Bias")
    print("=" * 60)
    print(f"Profile: {PROFILE}  Agent: {AGENT}  Workspace: {EXP_WS}")

    # Verify workspace/agent exist
    if not EXP_WS.exists():
        print(f"[ERROR] workspace {EXP_WS} not found. Run run_bias_experiment.py first.")
        return {}

    tiers = ["high", "medium", "low", "noise"]
    all_records = []

    for tier in tiers:
        anchor_desc = TIER_DESCS["high"]
        target_desc = TIER_DESCS[tier]
        anchor_name = "a_anchor_high"   # 'a_' prefix → alphabetically first
        target_name = f"b_target_{tier}" # 'b_' prefix → alphabetically second

        # Pre-compute expected similarity for first query
        sample_q = ZQ9_QUERIES[0]
        sims_pre = get_sim(sample_q, {anchor_name: anchor_desc, target_name: target_desc})
        print(f"\n[tier={tier}] expected cosine ({sample_q[:40]}...):")
        for n, s in sims_pre.items():
            print(f"  {n}: {s}")

        # Two orderings: anchor first (a_ < b_) and target first (swap names so target is 'a_')
        orderings = [
            ("anchor_first",
             [(anchor_name, anchor_desc), (target_name, target_desc)]),
            ("target_first",
             [(f"a_target_{tier}", target_desc), (f"b_anchor_high", anchor_desc)]),
        ]

        for order_label, pairs in orderings:
            names = [p[0] for p in pairs]
            descs_map = {p[0]: p[1] for p in pairs}

            clear_skills()
            for name, desc in pairs:
                install(name, desc)

            chars = count_desc_chars()
            mode = "COMPACT⚠️" if chars > 30000 else "normal✅"
            print(f"\n  [{order_label}] skills={names}  chars={chars}  {mode}")

            for q in ZQ9_QUERIES:
                sims = get_sim(q, descs_map)

                for rep in range(N_REPS):
                    res  = run_turn(q, names)
                    sel  = res["selected"]

                    # Determine which name is the "anchor" in this ordering
                    anchor_key = names[0] if "anchor" in names[0] else names[1]
                    target_key = names[0] if "target" in names[0] else names[1]

                    sim_anchor = sims.get(anchor_key)
                    sim_target = sims.get(target_key)
                    gap = round(sim_anchor - sim_target, 4) if (
                        sim_anchor is not None and sim_target is not None
                    ) else None

                    # correct = chose the higher-sim skill
                    if sel == anchor_key:
                        correct = (sim_anchor is None) or (sim_anchor >= sim_target)
                    elif sel == target_key:
                        correct = (sim_target is None) or (sim_target >= sim_anchor)
                    else:
                        correct = None  # no selection

                    rec = {
                        "tier": tier,
                        "order": order_label,
                        "query": q,
                        "rep": rep,
                        "selected": sel,
                        "anchor_name": anchor_key,
                        "target_name": target_key,
                        "sim_anchor": sim_anchor,
                        "sim_target": sim_target,
                        "sim_gap": gap,
                        "correct_choice": correct,
                    }
                    all_records.append(rec)

                    icon = ("✅" if correct else ("❌" if correct is False else "❓"))
                    gap_s = f"{gap:+.3f}" if gap is not None else "N/A"
                    print(
                        f"    {icon} [{order_label}][{tier}] rep{rep} "
                        f"→ {str(sel or 'NONE'):<22} "
                        f"| anchor={sim_anchor}  target={sim_target}  gap={gap_s}"
                    )
                    time.sleep(2)

    clear_skills()
    return {"exp_id": "C6_sim_threshold", "records": all_records}

# ── Analysis ──────────────────────────────────────────────────────────────────
def analyze(records: list[dict]):
    print("\n" + "─" * 60)
    print("C6 RESULTS SUMMARY")
    print("─" * 60)

    print(f"\n{'Tier':<10} {'N':>4} {'Selected%':>10} {'Correct%':>10} {'Mean gap':>10} {'Bias?'}")
    print("─" * 60)

    for tier in ["high", "medium", "low", "noise"]:
        recs = [r for r in records if r["tier"] == tier]
        if not recs:
            continue
        n = len(recs)
        selected  = sum(1 for r in recs if r["selected"])
        correct_n = [r for r in recs if r["correct_choice"] is not None]
        correct_pct = sum(1 for r in correct_n if r["correct_choice"]) / len(correct_n) * 100 if correct_n else 0
        gaps = [r["sim_gap"] for r in recs if r["sim_gap"] is not None]
        mean_gap = sum(gaps) / len(gaps) if gaps else 0.0

        # Detect position-overrides-similarity
        wrong_large = [r for r in correct_n if r["sim_gap"] and r["sim_gap"] > 0.20 and not r["correct_choice"]]
        large_gap_n = [r for r in correct_n if r["sim_gap"] and r["sim_gap"] > 0.20]
        bias_flag = (
            f"⚠️  pos bias: {len(wrong_large)}/{len(large_gap_n)} wrong at gap>0.20"
            if wrong_large else "✅  tracks sim"
        )

        print(f"{tier:<10} {n:>4} {selected/n*100:>9.0f}% {correct_pct:>9.1f}% {mean_gap:>+10.3f} {bias_flag}")

    # Threshold crossing table
    print("\nRouting correctness by sim_gap bucket (does agent track similarity?)")
    print(f"  {'gap≥':>6}  {'correct%':>9}  {'n':>4}  {'interpretation'}")
    boundaries = [(0.05, ">0.05"), (0.20, ">0.20"), (0.40, ">0.40"), (0.60, ">0.60")]
    for thr, label in boundaries:
        bucket = [r for r in records if r["sim_gap"] and r["sim_gap"] >= thr
                  and r["correct_choice"] is not None]
        if not bucket:
            continue
        pct = sum(1 for r in bucket if r["correct_choice"]) / len(bucket) * 100
        interp = "✅ semantic routing" if pct >= 80 else "⚠️  position/name bias"
        print(f"  {label:>6}  {pct:>8.1f}%  {len(bucket):>4}  {interp}")

    print("\nPosition bias breakdown (anchor_first vs target_first):")
    for order in ["anchor_first", "target_first"]:
        order_recs = [r for r in records if r["order"] == order and r["correct_choice"] is not None]
        if not order_recs:
            continue
        pct = sum(1 for r in order_recs if r["correct_choice"]) / len(order_recs) * 100
        print(f"  {order}: {pct:.1f}% correct  (n={len(order_recs)})")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    result = run_c6()
    if not result:
        return

    records = result.get("records", [])

    # Save
    out = RESULTS / "c6_sim_threshold_results.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\n✅ Saved {len(records)} records → {out}")

    # Analyze
    analyze(records)

if __name__ == "__main__":
    main()
