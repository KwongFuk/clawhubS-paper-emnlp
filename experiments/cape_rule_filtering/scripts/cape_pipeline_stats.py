#!/usr/bin/env python3
"""
CAPE Skill Pair 挖掘流水线统计
================================
Single-source-of-truth pipeline report.
All filter criteria imported from cape_filter_config.py.

Usage:
    .venv/bin/python experiments/cape_rule_filtering/scripts/cape_pipeline_stats.py
"""
import json, sqlite3, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cape_filter_config import (
    SINK_SEVERITY, MIN_SINK_SEV, TRIGGER_PATTERNS,
    HIGH_HIJACK_CLASSES, is_sink_candidate, OHS_THRESHOLD
)

CAPE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CAPE_ROOT.parents[1]

DB = REPO_ROOT / "data/experiment1_split/merged.sqlite3"
R  = CAPE_ROOT / "results"

SEP = "=" * 68

def fmt(n): return f"{n:>10,}"

print(SEP)
print("CAPE Skill Pair 挖掘流水线 — 各步精确统计")
print(f"Filter config: cape_filter_config.py  OHS threshold: {OHS_THRESHOLD}")
print(SEP)

con = sqlite3.connect(DB); con.row_factory = sqlite3.Row

# ── Step 0 ────────────────────────────────────────────────────────────────────
total = con.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
print(f"\n【Step 0】 数据集")
print(f"  ClawHub 全部 skills:           {fmt(total)}")

# ── Step 1 ────────────────────────────────────────────────────────────────────
profiles = {json.loads(l)['skill_id']: json.loads(l)
            for l in (R/"cape_rq2_profiles.jsonl").open()}
sources  = {s: p for s, p in profiles.items() if p.get('source_caps')}
sinks    = {s: p for s, p in profiles.items() if p.get('sink_caps')}
dual     = {s for s in profiles
            if profiles[s].get('source_caps') and profiles[s].get('sink_caps')}
chs      = {s: p for s, p in profiles.items() if p.get('channel_caps')}

src_dist = Counter(c for p in sources.values() for c in p.get('source_caps',[]))
snk_dist = Counter(c for p in sinks.values()   for c in p.get('sink_caps',[]))

print(f"\n【Step 1】 LSCPS 结构化能力提取 (SKILL.md → caps)")
print(f"  Source skills (引入危险数据):  {fmt(len(sources))}  ({100*len(sources)/total:.1f}%)")
print(f"  Sink skills   (执行危险操作):  {fmt(len(sinks))}  ({100*len(sinks)/total:.1f}%)")
print(f"  Channel-aware skills:          {fmt(len(chs))}  ({100*len(chs)/total:.1f}%)")
print(f"  双角色 skills (source+sink):   {fmt(len(dual))}  ({100*len(dual)/total:.1f}%)")
print(f"\n  Source capability 分布 (Top 5):")
for cap, n in src_dist.most_common(5):
    print(f"    {cap:<32} {n:>6,}")
print(f"\n  Sink capability 分布 (Top 5):")
for cap, n in snk_dist.most_common(5):
    sev = SINK_SEVERITY.get(cap, 0)
    threshold_mark = "✓" if sev >= MIN_SINK_SEV else "✗"
    print(f"    {threshold_mark} sev={sev}  {cap:<28} {n:>6,}")

# ── Step 2 ────────────────────────────────────────────────────────────────────
theory = len(sources) * len(sinks)
print(f"\n【Step 2】 理论 Pair 空间")
print(f"  Source({len(sources):,}) × Sink({len(sinks):,}):  {fmt(theory)}")

# ── Step 3 ────────────────────────────────────────────────────────────────────
dp = json.load((R/"directed_pairs_result.json").open())
print(f"\n【Step 3】 有向渠道过滤 (显式 source→channel→sink)")
print(f"  有向 pairs (stdout_pipe/tmp):  {fmt(dp['directed_pairs'])}")
print(f"  显式 channel pairs:            {fmt(dp['explicit_pairs'])}")
print(f"  全部可组合 pairs:              {fmt(dp['all_pairs'])}")

# ── Step 4 ────────────────────────────────────────────────────────────────────
# Recompute candidates using canonical config
candidates = []
for skill_id, p in sinks.items():
    row = con.execute(
        "SELECT name, downloads, description FROM skills WHERE skill_id=?",
        (skill_id,)
    ).fetchone()
    if not row: continue
    ok, hijack, matched = is_sink_candidate(p.get('sink_caps',[]), row['description'] or '')
    if ok:
        candidates.append({
            'skill_id': skill_id,
            'name': row['name'],
            'downloads': row['downloads'],
            'sink_caps': p['sink_caps'],
            'hijack': hijack,
            'matched': matched,
        })

# sev < MIN_SINK_SEV breakdown
below_sev = Counter()
for p in sinks.values():
    sev = max((SINK_SEVERITY.get(s, 0) for s in p.get('sink_caps',[])), default=0)
    if sev < MIN_SINK_SEV:
        for c in p.get('sink_caps', []):
            if SINK_SEVERITY.get(c, 0) < MIN_SINK_SEV:
                below_sev[c] += 1

adj = [json.loads(l) for l in (R/"full_adjudication_codex.jsonl").open()]
verdicts = Counter(a.get('verdict','?') for a in adj)
confirmed = [a for a in adj if a.get('verdict') in ('CRITICAL','CONFIRMED')]

print(f"\n【Step 4】 Sink 危险性筛选 (canonical config) & Codex 评判")
print(f"  过滤条件: max(sev) >= {MIN_SINK_SEV}  AND  hijack > 0")
print(f"  候选 sink skills:              {fmt(len(candidates))}")
print(f"  实际送 Codex 评判:             {fmt(len(adj))}  (含历史批次)")
print(f"  差距 ({len(adj)-len(candidates)}个): 不同脚本版本导致的历史误差 → 已统一")
print(f"\n  Verdict 分布:")
for v, n in verdicts.most_common():
    mark = "✅" if v in ('CRITICAL','CONFIRMED') else "  "
    print(f"    {mark} {v:<12} {n:>5,}")
print(f"\n  确认危险 (CRITICAL+CONFIRMED): {fmt(len(confirmed))}")
print(f"\n  ⚠ 未覆盖的 sink (sev<{MIN_SINK_SEV}，被过滤):")
print(f"    总计 {len(sinks)-len(candidates):,} 个，主要 cap:")
for cap, n in below_sev.most_common(5):
    sev = SINK_SEVERITY.get(cap, 0)
    note = "← 今日POC使用!" if cap == "file_write" else ""
    print(f"      sev={sev}  {cap:<28} {n:>5,}  {note}")

# ── Step 5 ────────────────────────────────────────────────────────────────────
ohs_pairs = [json.loads(l) for l in (R/"trigger_channel_pairs.jsonl").open()]
t_dist = Counter(p['sink_trigger_type'] for p in ohs_pairs)
ohs_dist = Counter(round(p['ohs'], 1) for p in ohs_pairs)

print(f"\n【Step 5】 OHS 评分配对  (threshold >= {OHS_THRESHOLD})")
print(f"  计算范围: {len(sources):,} sources × {len(confirmed):,} confirmed sinks")
print(f"  高危 pairs:                    {fmt(len(ohs_pairs))}")
print(f"\n  触发类型分布:")
for t, n in t_dist.most_common():
    pct = 100*n/len(ohs_pairs)
    bar = "█" * int(pct/2)
    print(f"    {t:<22} {n:>8,}  {pct:5.1f}%  {bar}")
print(f"\n  OHS 评分分布:")
for score in sorted(ohs_dist.keys(), reverse=True):
    bar = "█" * (ohs_dist[score] // 10000)
    print(f"    OHS={score:.1f}  {ohs_dist[score]:>8,}  {bar}")

# ── Step 6 ────────────────────────────────────────────────────────────────────
print(f"\n【Step 6】 POC 验证")
print(f"  Top 30 (adjudication 样本):            30")
print(f"  已 POC (web-fetch × memory-tiering):   1  ✅ 端到端成功")
print(f"  待验证:                          {fmt(len(ohs_pairs)-1)}")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print(f"流水线汇总（使用统一 cape_filter_config.py）")
print(f"{SEP}")
rows = [
    ("Step 0  全库 skills",             total,          ""),
    ("Step 1  有 source 能力",           len(sources),   f"({100*len(sources)/total:.1f}%)"),
    ("Step 1  有 sink 能力",             len(sinks),     f"({100*len(sinks)/total:.1f}%)"),
    ("Step 2  理论 pair 空间",           theory,         ""),
    ("Step 3  显式渠道 pairs",           dp['explicit_pairs'], ""),
    ("Step 4  sink 候选 (canonical)",    len(candidates),"← 统一后"),
    ("Step 4  Codex 评判 (historical)",  len(adj),       "← 历史批次"),
    ("Step 4  确认危险 sinks",           len(confirmed), ""),
    ("Step 5  高 OHS pairs",             len(ohs_pairs), f"(OHS≥{OHS_THRESHOLD})"),
    ("Step 6  POC 验证完成",             1,              "✅"),
]
for label, n, note in rows:
    print(f"  {label:<35} {n:>12,}  {note}")

con.close()
