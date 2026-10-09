#!/usr/bin/env python3
"""
Description Quality Assessment
================================
对 ClawHub 描述运行 5 项规则检测，输出质量分布。

质量维度：
  Q1 - 长度充足      (>80 chars)
  Q2 - 有触发条件    ("Use when", "Trigger", etc.)
  Q3 - 有边界约束    ("NOT for", "Do not use", etc.)
  Q4 - 功能具体      (包含动词+宾语，不只是类别标签)
  Q5 - 与 body 一致  (description 提到的能力在 body 里有对应)

综合质量分：0-5分，按分层输出分布
"""

import re, sqlite3, json
from pathlib import Path
from collections import Counter

DB = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUT = "/Users/guangfu/codex/clawhubS/skill-X/experiments/desc_importance/desc_quality_dist.json"

db = sqlite3.connect(DB)
cur = db.cursor()

cur.execute("""
  SELECT skill_slug, name, description, skill_text
  FROM skills
  WHERE description IS NOT NULL
    AND length(description) > 10
    AND skill_text IS NOT NULL
    AND length(skill_text) > 200
  LIMIT 5000
""")
rows = cur.fetchall()
print(f"Loaded {len(rows)} skills for assessment\n")

# ── Quality Checks ────────────────────────────────────────────────────────────

TRIGGER_PATTERNS = re.compile(
    r"Use when|use when|Trigger when|trigger when|trigger:|triggers:|"
    r"Use for:|For use when|When to use|Invoke when|Call when|"
    r"Use this when|Activate when",
    re.IGNORECASE
)

BOUNDARY_PATTERNS = re.compile(
    r"NOT for|not for|Do not use|don't use|Avoid when|"
    r"not intended|not suitable|not designed|Out of scope|"
    r"outside the scope|doesn't handle|does not handle",
    re.IGNORECASE
)

ACTION_VERBS = re.compile(
    r"\b(create|generate|manage|analyze|search|fetch|send|"
    r"monitor|deploy|run|execute|install|configure|build|"
    r"translate|summarize|extract|detect|remove|convert|"
    r"schedule|track|update|delete|list|show|get|post)\b",
    re.IGNORECASE
)

def assess(slug, name, desc, body):
    scores = {}

    # Q1: Length ≥ 80 chars
    scores["Q1_length"] = int(len(desc) >= 80)

    # Q2: Has trigger condition
    scores["Q2_trigger"] = int(bool(TRIGGER_PATTERNS.search(desc)))

    # Q3: Has boundary constraint
    scores["Q3_boundary"] = int(bool(BOUNDARY_PATTERNS.search(desc)))

    # Q4: Contains action verbs (specific, not just category labels)
    verbs = ACTION_VERBS.findall(desc)
    scores["Q4_specific"] = int(len(set(v.lower() for v in verbs)) >= 2)

    # Q5: Not just restating the name
    # If description starts with the slug or name, it's likely low-quality
    name_words = set(re.split(r"[\s\-_]+", (name or slug).lower()))
    desc_words  = set(re.split(r"\s+", desc.lower()[:60]))
    overlap = name_words & desc_words
    name_restate = len(overlap) / max(len(name_words), 1)
    scores["Q5_not_restate"] = int(name_restate < 0.7)

    scores["total"] = sum(v for k, v in scores.items() if k != "total")
    return scores

# ── Run ───────────────────────────────────────────────────────────────────────

results = []
score_counts = Counter()
dim_counts   = {f"Q{i}": 0 for i in range(1, 6)}

for slug, name, desc, body in rows:
    s = assess(slug, name, desc, body)
    score_counts[s["total"]] += 1
    for k in dim_counts:
        dim_key = [x for x in s if x.startswith(k)][0]
        dim_counts[k] += s[dim_key]
    results.append({"slug": slug, **s, "desc_len": len(desc)})

n = len(rows)

# ── Print results ─────────────────────────────────────────────────────────────
print("=" * 50)
print("DESCRIPTION QUALITY DISTRIBUTION")
print("=" * 50)

print("\n── 各维度通过率 ──")
dims = {
    "Q1": "长度 ≥80 chars",
    "Q2": "有触发条件 (Use when...)",
    "Q3": "有边界约束 (NOT for...)",
    "Q4": "功能具体 (≥2 action verbs)",
    "Q5": "不只是重述名称",
}
for k, label in dims.items():
    pct = dim_counts[k] / n * 100
    bar = "█" * int(pct / 5)
    print(f"  {k} {label:30s}  {pct:5.1f}%  {bar}")

print("\n── 综合质量分分布 (0-5) ──")
for score in range(6):
    cnt = score_counts[score]
    pct = cnt / n * 100
    bar = "█" * int(pct / 2)
    label = {0: "极差", 1: "差", 2: "偏低", 3: "一般", 4: "良好", 5: "优秀"}[score]
    print(f"  Score {score} [{label:4s}]  {cnt:5d} skills  {pct:5.1f}%  {bar}")

# Poor = 0-2, Medium = 3, Good = 4-5
poor   = sum(score_counts[i] for i in range(3))
medium = score_counts[3]
good   = sum(score_counts[i] for i in range(4, 6))
print(f"\n  Poor (0-2):   {poor:5d}  ({poor/n*100:.1f}%)")
print(f"  Medium (3):   {medium:5d}  ({medium/n*100:.1f}%)")
print(f"  Good (4-5):   {good:5d}  ({good/n*100:.1f}%)")

print("\n── 低质量 skill 样本 (score ≤ 1) ──")
low = [r for r in results if r["total"] <= 1][:5]
for r in low:
    slug = r["slug"]
    cur.execute("SELECT description FROM skills WHERE skill_slug=? LIMIT 1", (slug,))
    row = cur.fetchone()
    print(f"  {slug:35s} score={r['total']}  len={r['desc_len']}")
    if row:
        print(f"    desc: {row[0][:90]}")

print("\n── 高质量 skill 样本 (score = 5) ──")
high = [r for r in results if r["total"] == 5][:5]
for r in high:
    slug = r["slug"]
    cur.execute("SELECT description FROM skills WHERE skill_slug=? LIMIT 1", (slug,))
    row = cur.fetchone()
    print(f"  {slug:35s} score={r['total']}  len={r['desc_len']}")
    if row:
        print(f"    desc: {row[0][:90]}")

Path(OUT).write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f"\n→ 完整结果保存至 {OUT}")
