# ============================================================
# DEPRECATED — 2026-04-25
# ============================================================
# 方法论问题：此脚本从 skill DESCRIPTION 提取高层能力，从 skill BODY
# 提取细粒度能力，然后做对齐比较（CDI/CR/CP）。
#
# 根本缺陷（Granularity Mismatch）：
#   - 描述是 HIGH-LEVEL（"manages GitHub repositories"）
#   - Body 实现是 FINE-GRAINED（git.clone(), SSH key读写, API调用）
#
# 导致的问题：
#   1. IC-1 虚假高发：几乎所有描述都会被判定为"underdisclosure"，
#      因为描述本来就不该列举每个实现细节。(99.2% 结论不可信)
#   2. CDI/CR/CP 指标失效：分子分母在不同粒度层，无法反映真实对齐度。
#   3. IC 分类矩阵（Description vs Body）本身假设两端对等，实际不成立。
#
# 后续实验应采用以下替代方案之一：
#   - 基于 ABUSE PATTERN 的直接分析（T2/T5/T6 approach）
#   - 路由实验（routing gap）验证实际危害
#   - 人工标注对齐（仅对 body 声明的 high-level 功能做描述核查）
# ============================================================

#!/usr/bin/env python3
"""
50-skill pilot: probe prose-strip + joint-extract strategy for issues.
Prints per-skill results + flags anomalies for human review.
"""

import json, re, sqlite3, subprocess, time
from pathlib import Path
from collections import Counter

DB     = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUT    = Path(__file__).parent / "results/cap_alignment_v2/pilot_50.jsonl"
OUT.parent.mkdir(parents=True, exist_ok=True)

# ── Body excerpt: strip code + find functional prose ─────────────────────────

def smart_excerpt(body: str, target: int = 3000) -> str:
    body = re.sub(r'^---[\s\S]*?---\n', '', body, flags=re.MULTILINE).strip()
    body = re.sub(r'```[\s\S]*?```', '', body, flags=re.MULTILINE)
    body = re.sub(r'`[^`]+`', '', body)
    body = re.sub(r'\n{3,}', '\n\n', body)

    lines = body.split('\n')
    SKIP = re.compile(
        r'(status\s*:|last updated|latest version|prerequisites|^installation$|'
        r'npm install|pip install|brew install|version\s*\d|compatibility|'
        r'getting started|requirements\s*$|node\.js|python 3|dotnet tool)',
        re.IGNORECASE)
    FUNC = re.compile(
        r'^#+\s+(command|feature|capabilit|usage|operation|overview|what|how|'
        r'support|core|main|function|key|guide|reference|tool)',
        re.IGNORECASE)

    start = 0
    for i, line in enumerate(lines[:150]):
        if i > 12 and FUNC.match(line.strip()) and not SKIP.search(line):
            start = i
            break
    if start == 0:
        start = max(0, len(lines) // 7)

    prose = '\n'.join(lines[start:])
    prose = re.sub(r'\n{3,}', '\n\n', prose)
    return prose[:target]


# ── Prompt ────────────────────────────────────────────────────────────────────

JOINT = """\
Extract capability lists from two sources of the same AI skill.
Use UNIFIED vocabulary — use the SAME phrase for the same concept in both lists.

SOURCE A — marketplace description:
{description}

SOURCE B — skill documentation (code stripped):
{excerpt}

Output short noun phrases (2-6 words) for each distinct functional capability.

Return ONLY valid JSON:
{{"c_desc": [...], "c_body": [...]}}

If a concept appears in both sources, use IDENTICAL wording in both lists."""


# ── LLM ──────────────────────────────────────────────────────────────────────

def llm(prompt: str) -> dict | None:
    for _ in range(3):
        try:
            r = subprocess.run(
                ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120)
            text = json.loads(r.stdout).get("outputs", [{}])[0].get("text", "").strip()
            for part in text.split("```"):
                part = part.strip().lstrip("json").strip()
                try: return json.loads(part)
                except: pass
            return json.loads(text)
        except subprocess.TimeoutExpired:
            time.sleep(5)
        except Exception:
            pass
    return None


def safe_prompt(template: str, **kwargs) -> str:
    """Safe substitution: replaces {key} markers without interpreting other {braces}."""
    result = template
    for k, v in kwargs.items():
        result = result.replace('{' + k + '}', str(v))
    return result


# ── IC metrics ────────────────────────────────────────────────────────────────

def compute_ic(c_desc, c_body):
    shared    = set(c_desc) & set(c_body)
    only_desc = set(c_desc) - set(c_body)
    only_body = set(c_body) - set(c_desc)
    cr  = len(shared)    / max(len(c_body), 1)
    cp  = 1 - len(only_desc) / max(len(c_desc), 1)
    cdi = 1 - (cr + cp) / 2
    ic  = ("IC-0"   if cr >= 1.0 and cp >= 1.0 else
           "IC-1+4" if cr < 1.0  and cp < 1.0  else
           "IC-1"   if cr < 1.0  else "IC-4")
    return dict(
        shared=sorted(shared), only_desc=sorted(only_desc), only_body=sorted(only_body),
        CR=round(cr,3), CP=round(cp,3), CDI=round(cdi,3), ic_type=ic)


# ── Anomaly flags (for human review) ─────────────────────────────────────────

def flag_anomalies(slug, desc, body, excerpt, c_desc, c_body, ic):
    flags = []
    # Almost all code — excerpt very short after strip
    if len(excerpt) < 300:
        flags.append("EXCERPT_TOO_SHORT")
    # Extraction returned very few caps (LLM confused?)
    if len(c_desc) < 3 or len(c_body) < 3:
        flags.append("TOO_FEW_CAPS")
    # CR near 0: probably wrong excerpt section
    if ic["CR"] < 0.15:
        flags.append("CR_NEAR_ZERO")
    # CP near 0: almost all desc claims not in body – overclaiming or wrong extract
    if ic["CP"] < 0.20:
        flags.append("CP_NEAR_ZERO")
    # c_body much larger than c_desc — might be extracting too broadly
    if len(c_body) > 3 * max(len(c_desc), 1):
        flags.append("BODY_EXPLODED")
    # desc == body (same text stored twice in DB)
    if desc.strip() == body.strip():
        flags.append("DESC_EQ_BODY")
    return flags


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    db = sqlite3.connect(DB)
    cur = db.cursor()
    cur.execute("""
      SELECT skill_slug, description, skill_text
      FROM skills
      WHERE description IS NOT NULL
        AND skill_text IS NOT NULL
        AND description != skill_text
        AND length(description) BETWEEN 80 AND 1200
        AND length(skill_text)  BETWEEN 500 AND 30000
        AND description NOT LIKE '%ERROR%'
        AND description NOT LIKE '%Insert instructions%'
        AND description NOT LIKE '%Example%'
      ORDER BY RANDOM()
      LIMIT 150
    """)
    rows = cur.fetchall()
    db.close()

    seen, pool = set(), []
    for slug, d, b in rows:
        if slug not in seen:
            seen.add(slug); pool.append((slug, d, b))
        if len(pool) == 50: break

    print(f"Pool: {len(pool)} skills\n")

    # Resume
    done, results = set(), []
    if OUT.exists():
        for line in OUT.read_text().strip().split('\n'):
            try:
                r = json.loads(line); done.add(r['slug']); results.append(r)
            except: pass

    todo = [(s,d,b) for s,d,b in pool if s not in done]
    print(f"Done: {len(done)} | Remaining: {len(todo)}\n")

    with open(OUT, 'a') as f:
        for i, (slug, desc, body) in enumerate(todo):
            excerpt = smart_excerpt(body)
            print(f"[{i+1}/{len(todo)}] {slug}  body={len(body)} excerpt={len(excerpt)}")

            result = llm(safe_prompt(JOINT, description=desc, excerpt=excerpt))
            if not result:
                rec = {"slug": slug, "error": "llm_failed",
                       "desc_len": len(desc), "body_len": len(body), "excerpt_len": len(excerpt)}
                results.append(rec); f.write(json.dumps(rec, ensure_ascii=False) + '\n'); f.flush()
                print("  [FAILED]\n"); continue

            c_desc = result.get("c_desc", [])
            c_body = result.get("c_body", [])
            ic     = compute_ic(c_desc, c_body)
            flags  = flag_anomalies(slug, desc, body, excerpt, c_desc, c_body, ic)

            rec = {
                "slug": slug, "desc_len": len(desc), "body_len": len(body),
                "excerpt_len": len(excerpt), "n_desc": len(c_desc), "n_body": len(c_body),
                "c_desc": c_desc, "c_body": c_body, "flags": flags, **ic
            }
            results.append(rec); f.write(json.dumps(rec, ensure_ascii=False) + '\n'); f.flush()

            flag_str = f"  ⚠ {flags}" if flags else ""
            print(f"  IC={ic['ic_type']:8s} CR={ic['CR']:.2f} CP={ic['CP']:.2f} CDI={ic['CDI']:.2f}"
                  f"  desc={len(c_desc)} body={len(c_body)}{flag_str}")
            if ic['only_body']:
                print(f"  Undisclosed: {ic['only_body'][:3]}")
            if ic['only_desc']:
                print(f"  Overclaimed: {ic['only_desc'][:3]}")
            print()
            time.sleep(0.3)

    # ── Summary ───────────────────────────────────────────────────────────────
    valid = [r for r in results if 'error' not in r]
    flagged = [r for r in valid if r.get('flags')]
    ic_dist = Counter(r['ic_type'] for r in valid)
    all_flags = Counter(f for r in valid for f in r.get('flags', []))

    print('=' * 65)
    print(f"Valid: {len(valid)} / {len(results)}\n")
    print("IC Distribution:")
    for ic, n in sorted(ic_dist.items()):
        print(f"  {ic:8s} {n:4d} ({n/len(valid)*100:.1f}%)")

    avg = lambda k: sum(r.get(k, 0) for r in valid) / max(len(valid), 1)
    print(f"\nMeans: CR={avg('CR'):.3f}  CP={avg('CP'):.3f}  CDI={avg('CDI'):.3f}")
    print(f"       n_desc={avg('n_desc'):.1f}  n_body={avg('n_body'):.1f}")

    if all_flags:
        print(f"\nAnomaly flags ({len(flagged)} flagged skills):")
        for flag, n in all_flags.most_common():
            print(f"  {flag:25s} {n}")
        print("\nFlagged skills:")
        for r in flagged:
            print(f"  {r['slug']:40s} {r['flags']}")

if __name__ == "__main__":
    main()
