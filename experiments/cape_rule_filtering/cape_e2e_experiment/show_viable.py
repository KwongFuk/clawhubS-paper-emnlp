#!/usr/bin/env python3
import json
from collections import Counter, defaultdict

data = json.load(open("ss_full_viable.json"))
skills = data["skills"]
print(f"Total viable: {len(skills)}\n")

by_score = Counter(s.get("danger_score", 0) for s in skills)
print("Danger score:")
for score in sorted(by_score, reverse=True):
    print(f"  {score}/10: {by_score[score]:3d}  {'>' * by_score[score]}")
print()

src_types  = ["S1_web_fetch", "S2_user_input", "S3_agent_memory", "S4_local_system"]
sink_types = ["K1_shell_exec", "K2_file_write", "K3_network_exfil", "K4_memory_poison", "K5_persistence"]
src_sink   = defaultdict(int)
for s in skills:
    src_sink[(s.get("source_type", "?"), s.get("sink_type", "?"))] += 1

hdr = "".join(f"{k[3:13]:>12s}" for k in sink_types)
print("Source × Sink matrix:")
print(f"{'':22s}{hdr}")
for src in src_types:
    row = "".join(f"{src_sink[(src, snk)]:12d}" for snk in sink_types)
    print(f"  {src[3:]:20s}{row}")
print()

print("TOP 20 by danger_score:")
for i, s in enumerate(skills[:20], 1):
    print(f"  {i:2d}. [{s['danger_score']}/10] {s['skill_id'][:55]}")
    print(f"       {s.get('source_type','?')[:5]} → {s.get('sink_type','?')}")
    print(f"       {s.get('exploit_path','')[:75]}")
