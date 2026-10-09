#!/usr/bin/env python3
"""
ss_pair_filter.py — Feasibility Filter for skills.sh candidate pairs
=====================================================================
From the 200 candidate pairs, selects pairs that are ACTUALLY EXECUTABLE
in the CAPE v2 experiment framework.

Feasibility Criteria (all must pass):
  F1: Skill is installable via openclaw (has install_command)
  F2: Skill has skill_text > 200 chars (enough content to inject)
  F3: Source type is S1 or S2 (proven attack paths)
     OR source type is S3/S4 with high-confidence evidence
  F4: Sink type is K1, K2, or K3 (shortest probe path)
  F5: Downloads >= 1000 (real-world usage)
  F6: Description length >= 30 (agent has enough context)
  F7: Not a test/demo skill (filter by name patterns)

Output:
  ss_viable_pairs.json    — filtered, ranked, ready-to-run pairs
  ss_filter_report.json   — filter statistics

Usage:
  python3 ss_pair_filter.py
  python3 ss_pair_filter.py --top 36   # match ClawHub CAPE v2 size
"""
from __future__ import annotations
import json, sqlite3, re
from pathlib import Path
from collections import defaultdict

DB_PATH    = Path.home() / "codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
HERE       = Path(__file__).parent
IN_PAIRS   = HERE / "ss_candidate_pairs.json"
OUT_VIABLE = HERE / "ss_viable_pairs.json"
OUT_REPORT = HERE / "ss_filter_report.json"

# Skills that are clearly test/demo artifacts
DEMO_PATTERNS = [re.compile(p, re.I) for p in [
    r'\btest\b', r'\bdemo\b', r'\bexample\b', r'\bsample\b',
    r'\bhello.?world\b', r'\bmy.first\b', r'\btemplate\b',
    r'\bplaceholder\b', r'\bfoo\b', r'\bbar\b',
]]

# Source types that work with proven CAPE channels
HIGH_CONFIDENCE_SOURCES = {
    "S1_web_fetch",   # 100% success rate, curl→URL pattern
    "S2_user_input",  # 33% success, user provides URL
}
MEDIUM_CONFIDENCE_SOURCES = {
    "S3_agent_memory",  # 0% currently, but memory path works in theory
    "S4_local_system",  # 0% currently, file path works
}

# Sink types with cleanest probe (K1 = shell, K2 = file write, K3 = net)
PREFERRED_SINKS = {"K1_shell_exec", "K2_file_write", "K3_network_exfil"}

def load_skill_meta(skill_ids: list[str]) -> dict:
    """Load install_command, downloads, description length from DB."""
    con = sqlite3.connect(str(DB_PATH))
    placeholders = ",".join("?" * len(skill_ids))
    rows = con.execute(f"""
        SELECT skill_id, install_command, COALESCE(downloads, total_installs, 0),
               description, length(skill_text), length(description), name
        FROM skills
        WHERE skill_id IN ({placeholders})
    """, skill_ids).fetchall()
    con.close()
    return {
        r[0]: {
            "install_cmd": r[1] or "",
            "downloads":   r[2] or 0,
            "description": r[3] or "",
            "text_len":    r[4] or 0,
            "desc_len":    r[5] or 0,
            "name":        r[6] or r[0].split("/")[-1],
        }
        for r in rows
    }


def is_demo_skill(name: str, description: str) -> bool:
    text = (name + " " + description).lower()
    return any(p.search(text) for p in DEMO_PATTERNS)


def feasibility_score(pair: dict, src_meta: dict, sink_meta: dict) -> tuple[bool, int, list[str]]:
    """
    Returns (is_viable, reason_score, fail_reasons).
    reason_score: higher = better.
    """
    fails = []
    score = pair["score"]

    src = src_meta
    snk = sink_meta

    # F1: Install command
    if not src.get("install_cmd"):
        fails.append("src_no_install")
    if not snk.get("install_cmd"):
        fails.append("sink_no_install")

    # F2: Skill text length
    if src.get("text_len", 0) < 200:
        fails.append(f"src_text_short({src.get('text_len',0)})")
    if snk.get("text_len", 0) < 200:
        fails.append(f"sink_text_short({snk.get('text_len',0)})")

    # F3: Source type confidence
    src_type = pair["source_type"]
    if src_type in HIGH_CONFIDENCE_SOURCES:
        score += 20
    elif src_type in MEDIUM_CONFIDENCE_SOURCES:
        score += 5
    else:
        fails.append(f"unknown_src_type({src_type})")

    # F4: Preferred sink type
    sink_type = pair["sink_type"]
    if sink_type in PREFERRED_SINKS:
        score += 15
    else:
        score += 5  # other sinks still ok

    # F5: Downloads
    min_dl = min(src.get("downloads", 0), snk.get("downloads", 0))
    if min_dl < 1000:
        fails.append(f"low_downloads({min_dl})")
    elif min_dl >= 10000:
        score += 10
    elif min_dl >= 1000:
        score += 5

    # F6: Description length
    if src.get("desc_len", 0) < 30:
        fails.append("src_no_desc")

    # F7: Demo/test skill filter
    if is_demo_skill(src.get("name",""), src.get("description","")):
        fails.append("src_is_demo")
    if is_demo_skill(snk.get("name",""), snk.get("description","")):
        fails.append("sink_is_demo")

    # Bonus: self-exfil is simpler (one skill to install)
    if pair.get("type") == "self_exfil":
        score += 10

    # IC-1 gap family richness
    n_hidden = len(pair.get("ic1_gap_families", []))
    score += n_hidden * 3

    is_viable = len(fails) == 0
    return is_viable, score, fails


def main():
    data = json.loads(IN_PAIRS.read_text())
    pairs = data["pairs"]
    print(f"Loaded {len(pairs)} candidate pairs from {IN_PAIRS.name}")

    # Collect all unique skill IDs
    skill_ids = set()
    for p in pairs:
        skill_ids.add(p["source"]["skill_id"])
        skill_ids.add(p["sink"]["skill_id"])
    skill_ids = list(skill_ids)
    print(f"Loading metadata for {len(skill_ids)} unique skills...")
    meta = load_skill_meta(skill_ids)

    # Apply feasibility filter
    viable, rejected = [], []
    fail_counter = defaultdict(int)

    for pair in pairs:
        src_meta  = meta.get(pair["source"]["skill_id"], {})
        sink_meta = meta.get(pair["sink"]["skill_id"], {})
        is_ok, score, fails = feasibility_score(pair, src_meta, sink_meta)

        # Enrich pair with metadata
        pair["feasibility_score"] = score
        pair["fail_reasons"]      = fails
        pair["source"]["install_cmd"] = src_meta.get("install_cmd", "")
        pair["sink"]["install_cmd"]   = sink_meta.get("install_cmd", "")
        pair["source"]["text_len"]    = src_meta.get("text_len", 0)
        pair["sink"]["text_len"]      = sink_meta.get("text_len", 0)

        if is_ok:
            viable.append(pair)
        else:
            rejected.append(pair)
            for f in fails:
                # Strip dynamic parts for counting
                key = re.sub(r'\(.*\)', '', f)
                fail_counter[key] += 1

    viable.sort(key=lambda x: -x["feasibility_score"])

    # Save output
    output = {
        "version": "ss_filter_v1",
        "total_input": len(pairs),
        "viable_count": len(viable),
        "rejected_count": len(rejected),
        "pairs": viable,
    }
    OUT_VIABLE.write_text(json.dumps(output, indent=2, ensure_ascii=False))

    report = {
        "total_input":   len(pairs),
        "viable_count":  len(viable),
        "rejected_count": len(rejected),
        "reject_reasons": dict(sorted(fail_counter.items(), key=lambda x: -x[1])),
        "viable_source_dist": dict(defaultdict(int, {
            st: sum(1 for p in viable if p["source_type"] == st)
            for st in set(p["source_type"] for p in viable)
        })),
        "viable_sink_dist": dict(defaultdict(int, {
            kt: sum(1 for p in viable if p["sink_type"] == kt)
            for kt in set(p["sink_type"] for p in viable)
        })),
    }
    OUT_REPORT.write_text(json.dumps(report, indent=2))

    # ── Print summary ────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"Feasibility Filter Results")
    print(f"{'='*65}")
    print(f"  Input pairs:    {len(pairs)}")
    print(f"  Viable pairs:   {len(viable)}")
    print(f"  Rejected:       {len(rejected)}")
    print(f"\nTop rejection reasons:")
    for r, n in sorted(fail_counter.items(), key=lambda x: -x[1])[:8]:
        print(f"  {r:<35} {n:>4}")

    print(f"\n{'─'*65}")
    print(f"Top {min(20, len(viable))} Viable Pairs:")
    print(f"{'─'*65}")
    for i, p in enumerate(viable[:20]):
        src = p["source"]
        snk = p["sink"]
        print(f"  [{p['pair_id']}] fscore={p['feasibility_score']} type={p['type']}")
        print(f"    SRC: {src['skill_id'][:55]}")
        print(f"         install: {src.get('install_cmd','')[:50]}")
        print(f"    SINK:{snk['skill_id'][:55]}")
        print(f"         IC-1 gap: {p['ic1_gap_families']}")
        print(f"    {p['source_type']} → {p['sink_type']}")
        print()

    print(f"Viable pairs saved → {OUT_VIABLE}")
    print(f"Report → {OUT_REPORT}")


if __name__ == "__main__":
    main()
