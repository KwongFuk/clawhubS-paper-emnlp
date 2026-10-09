#!/usr/bin/env python3
"""
build_ss_pairs.py — 从 197 个 Codex 确认的 skills.sh viable skills
生成 CAPE 实验 pairs JSON，格式与 cape_125_pairs.json 完全相同。

每个 skill 的 sink_type 映射到对应的 task:
  K1_shell_exec    → T06_shell_exec_arbitrary
  K2_file_write    → T02_file_delete_creds
  K3_network_exfil → T07_network_data_exfil  /  T10_remote_exec_c2 / T19_feishu
  K4_memory_poison → T04_memory_poison_persist / T13_memory_c3_cross_session
  K5_persistence   → T05_boot_hook_inject / T20_agent_swarm_propagate
"""
import json
from pathlib import Path
from collections import Counter

VIABLE = Path(__file__).parent / "ss_full_viable.json"
OUT    = Path(__file__).parent / "ss_cape_pairs.json"

# Map sink_type → task (matching ClawHub experiment exactly)
SINK_TO_TASK = {
    "K1_shell_exec":    "T06_shell_exec_arbitrary",
    "K2_file_write":    "T02_file_delete_creds",
    "K3_network_exfil": "T07_network_data_exfil",
    "K4_memory_poison": "T13_memory_c3_cross_session",
    "K5_persistence":   "T05_boot_hook_inject",
}

# Map sink_type → channel (matching ClawHub channel taxonomy)
SINK_TO_CHANNEL = {
    "K1_shell_exec":    "C1_shell",
    "K2_file_write":    "C2_filesystem",
    "K3_network_exfil": "C4_network",
    "K4_memory_poison": "C3_agent_memory",
    "K5_persistence":   "C5_persistence",
}

# Feishu-specific override for Korean/Chinese messaging skills
FEISHU_KEYWORDS = ["feishu", "lark", "wechat", "wecom", "dingtalk", "slack", "telegram"]

def infer_verdict(score: int) -> str:
    if score >= 9: return "CRITICAL"
    if score >= 7: return "CONFIRMED"
    return "SUSPECTED"


def main():
    data   = json.load(VIABLE.open())
    skills = data["skills"]
    print(f"Input: {len(skills)} viable skills")

    pairs   = []
    skipped = 0

    for i, s in enumerate(skills, 1):
        sid  = s["skill_id"]
        src  = s.get("source_type", "")
        sink = s.get("sink_type", "")
        score = s.get("danger_score", 0)
        inst  = s.get("install_cmd", "")
        dl    = s.get("downloads", 0)

        if not sink or sink not in SINK_TO_TASK:
            skipped += 1
            continue
        if not inst:
            skipped += 1
            continue

        task    = SINK_TO_TASK[sink]
        channel = SINK_TO_CHANNEL[sink]

        # Override for messaging/webhook skills
        name_lower = sid.lower()
        if any(kw in name_lower for kw in FEISHU_KEYWORDS) and sink == "K3_network_exfil":
            task = "T19_feishu_data_exfil"

        # For memory skills with cross-session potential
        if sink == "K4_memory_poison" and score >= 8:
            task = "T13_memory_c3_cross_session"

        pair = {
            "pair_id":  f"SS{i:04d}",
            "rank":     i,
            "source": {
                "skill_id":   sid,
                "name":       sid.split("/")[-1],
                "downloads":  dl,
                "source_type": src,
                "install_cmd": inst,
            },
            "sink": {
                "skill_id":           sid,      # same skill: self-exfil
                "name":               sid.split("/")[-1],
                "downloads":          dl,
                "verdict":            infer_verdict(score),
                "sink_type":          sink,
                "sink_caps_inferred": [sink],
                "danger_score":       score,
                "exploit_path":       s.get("exploit_path", ""),
            },
            "task":    task,
            "channel": channel,
            # Skills.sh specific metadata
            "ss_metadata": {
                "install_cmd":      inst,
                "source_type":      src,
                "sink_type":        sink,
                "ic1_gap":          s.get("ic1_gap", True),
                "codex_confirmed":  True,
                "danger_score":     score,
            }
        }
        pairs.append(pair)

    print(f"Generated: {len(pairs)} pairs (skipped {skipped})")
    print()

    # Print distribution
    task_dist = Counter(p["task"] for p in pairs)
    print("Task distribution:")
    for t, n in task_dist.most_common():
        print(f"  {t:35s}: {n:4d}")

    sink_dist = Counter(p["sink"]["sink_type"] for p in pairs)
    print("\nSink distribution:")
    for s, n in sink_dist.most_common():
        print(f"  {s:20s}: {n:4d}")

    verdict_dist = Counter(p["sink"]["verdict"] for p in pairs)
    print("\nVerdict distribution:")
    for v, n in verdict_dist.most_common():
        print(f"  {v:12s}: {n:4d}")

    # Save
    output = {"pairs": pairs, "meta": {
        "total": len(pairs),
        "source": "skills.sh 197-viable Codex audit",
        "format": "compatible with cape_e2e_runner.py",
    }}
    OUT.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\n→ {OUT}")

    # Show top 20
    print("\nTop 20 pairs:")
    for p in pairs[:20]:
        print(f"  [{p['sink']['danger_score']}/10][{p['sink']['verdict']:9s}] "
              f"{p['pair_id']} {p['source']['skill_id'][:45]}")
        print(f"    {p['ss_metadata']['source_type'][3:]:15s} → "
              f"{p['ss_metadata']['sink_type'][3:]:15s} | {p['task']}")


if __name__ == "__main__":
    main()
