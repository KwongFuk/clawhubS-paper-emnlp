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
Capability Alignment Test — High-Level Semantic Comparison
===========================================================
New approach to fix granularity mismatch in IC detection:

  1. Extract high-level functional capabilities from DESCRIPTION → C_desc
  2. Extract high-level functional capabilities from BODY        → C_body
  3. LLM semantic alignment: find what's in C_body but not C_desc (IC-1)
                                           what's in C_desc but not C_body (IC-4)

Uses openclaw MCP (gpt-5.3-codex). Tests on 100 non-official ClawHub skills.

Output: results/cap_alignment_100.jsonl + summary report
"""

import json, sqlite3, subprocess, time, random
from pathlib import Path
from collections import Counter

DB      = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUTDIR  = Path(__file__).parent / "results" / "cap_alignment"
OUTFILE = OUTDIR / "cap_alignment_100.jsonl"
REPORT  = OUTDIR / "summary.json"

EXTRACT_DESC_PROMPT = """\
Extract the high-level functional capabilities declared in this skill description.
List ONLY capabilities that are clearly stated — do not infer from context.
Use short noun phrases (2-5 words each), e.g., "workbook creation", "chart generation".
Return JSON: {{"capabilities": ["cap1", "cap2", ...]}}

Description:
{description}"""

EXTRACT_BODY_PROMPT = """\
Extract the high-level functional capabilities supported by this skill based on its full documentation.
Include ALL distinct capabilities you can identify from the body.
Use short noun phrases (2-5 words each), e.g., "workbook creation", "chart generation".
Return JSON: {{"capabilities": ["cap1", "cap2", ...]}}

Skill body (first 2500 chars):
{body}"""

ALIGN_PROMPT = """\
Compare two capability lists for the same AI skill and classify the alignment.

C_desc (from marketplace description):
{c_desc}

C_body (from actual skill code/documentation):
{c_body}

For each capability in C_body, determine if C_desc covers it (exact or semantically equivalent).
For each capability in C_desc, determine if C_body supports it.

Return JSON:
{{
  "undisclosed": ["cap in C_body NOT covered by C_desc"],
  "overclaimed": ["cap in C_desc NOT supported by C_body"],
  "aligned": ["cap covered in both"],
  "ic_type": "IC-0" | "IC-1" | "IC-4" | "IC-1+4",
  "summary": "one sentence explaining the main mismatch or alignment"
}}

ic_type rules:
  IC-0: no significant mismatch
  IC-1: undisclosed capabilities exist (body has more than description)
  IC-4: overclaimed capabilities exist (description claims more than body)
  IC-1+4: both undisclosed and overclaimed"""


def llm(prompt: str) -> str | None:
    for attempt in range(3):
        try:
            r = subprocess.run(
                ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120,
            )
            j = json.loads(r.stdout)
            return j.get("outputs", [{}])[0].get("text", "").strip()
        except subprocess.TimeoutExpired:
            print(f"    [WARN] timeout attempt {attempt+1}/3")
            time.sleep(5)
        except Exception as e:
            print(f"    [ERR] {e}")
    return None


def parse_json(text: str) -> dict:
    if not text:
        return {}
    # Strip markdown fences
    if "```" in text:
        for part in text.split("```"):
            part = part.strip().lstrip("json").strip()
            try:
                return json.loads(part)
            except Exception:
                continue
    try:
        return json.loads(text)
    except Exception:
        return {}


def process_skill(slug: str, description: str, body: str) -> dict:
    body_excerpt = body[:2500]

    # Step 1: Extract C_desc
    r1 = parse_json(llm(EXTRACT_DESC_PROMPT.format(description=description)))
    c_desc = r1.get("capabilities", [])

    # Step 2: Extract C_body
    r2 = parse_json(llm(EXTRACT_BODY_PROMPT.format(body=body_excerpt)))
    c_body = r2.get("capabilities", [])

    if not c_desc or not c_body:
        return {"slug": slug, "error": "extraction_failed",
                "c_desc": c_desc, "c_body": c_body}

    # Step 3: Semantic alignment
    r3 = parse_json(llm(ALIGN_PROMPT.format(
        c_desc=json.dumps(c_desc, ensure_ascii=False),
        c_body=json.dumps(c_body, ensure_ascii=False),
    )))

    return {
        "slug":        slug,
        "desc_len":    len(description),
        "body_len":    len(body),
        "c_desc":      c_desc,
        "c_body":      c_body,
        "n_desc":      len(c_desc),
        "n_body":      len(c_body),
        "undisclosed": r3.get("undisclosed", []),
        "overclaimed": r3.get("overclaimed", []),
        "aligned":     r3.get("aligned", []),
        "ic_type":     r3.get("ic_type", "unknown"),
        "summary":     r3.get("summary", ""),
        "n_undisclosed": len(r3.get("undisclosed", [])),
        "n_overclaimed": len(r3.get("overclaimed", [])),
        "n_aligned":     len(r3.get("aligned", [])),
    }


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)

    # Load 100 non-official skills (random sample, desc+body both present)
    db = sqlite3.connect(DB)
    cur = db.cursor()
    cur.execute("""
      SELECT skill_slug, description, skill_text
      FROM skills
      WHERE description IS NOT NULL
        AND skill_text IS NOT NULL
        AND description != skill_text
        AND length(description) BETWEEN 80 AND 1000
        AND length(skill_text) > 800
        AND description NOT LIKE '%ERROR%'
        AND description NOT LIKE '%Insert instructions%'
      ORDER BY RANDOM()
      LIMIT 200
    """)
    rows = cur.fetchall()
    db.close()

    # Take first 100 after dedup
    seen, pool = set(), []
    for slug, desc, body in rows:
        if slug not in seen:
            seen.add(slug)
            pool.append((slug, desc, body))
        if len(pool) == 100:
            break

    print(f"Pool: {len(pool)} skills to process\n")

    # Resume
    done = set()
    results = []
    if OUTFILE.exists():
        with open(OUTFILE) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done.add(r["slug"])
                    results.append(r)
                except Exception:
                    pass

    todo = [(slug, desc, body) for slug, desc, body in pool if slug not in done]
    print(f"Already done: {len(done)} | Remaining: {len(todo)}\n")

    with open(OUTFILE, "a") as f:
        for i, (slug, desc, body) in enumerate(todo):
            print(f"[{i+1}/{len(todo)}] {slug}  (desc={len(desc)}, body={len(body)})")

            result = process_skill(slug, desc, body)
            results.append(result)
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()

            ic = result.get("ic_type", "?")
            nd = result.get("n_undisclosed", 0)
            no = result.get("n_overclaimed", 0)
            na = result.get("n_aligned", 0)
            print(f"  IC={ic}  undisclosed={nd}  overclaimed={no}  aligned={na}")
            if result.get("summary"):
                print(f"  {result['summary'][:90]}")
            print()
            time.sleep(0.5)

    # Summary
    valid = [r for r in results if "error" not in r]
    ic_counts = Counter(r.get("ic_type", "unknown") for r in valid)
    avg_undisclosed = sum(r.get("n_undisclosed", 0) for r in valid) / max(len(valid), 1)
    avg_overclaimed = sum(r.get("n_overclaimed", 0) for r in valid) / max(len(valid), 1)
    avg_aligned     = sum(r.get("n_aligned", 0)     for r in valid) / max(len(valid), 1)

    print("=" * 60)
    print(f"RESULTS: {len(valid)} skills")
    print(f"\nIC Type Distribution:")
    for ic, n in ic_counts.most_common():
        print(f"  {ic:8s}  {n:4d}  ({n/len(valid)*100:.1f}%)")
    print(f"\nAvg capabilities per skill:")
    print(f"  C_desc:      {sum(r.get('n_desc',0) for r in valid)/len(valid):.1f}")
    print(f"  C_body:      {sum(r.get('n_body',0) for r in valid)/len(valid):.1f}")
    print(f"  Aligned:     {avg_aligned:.1f}")
    print(f"  Undisclosed: {avg_undisclosed:.1f}  (IC-1 gap)")
    print(f"  Overclaimed: {avg_overclaimed:.1f}  (IC-4 gap)")

    coverage_ratio = avg_aligned / max(avg_aligned + avg_undisclosed, 1)
    print(f"\nDescription Coverage: {coverage_ratio*100:.1f}%  (aligned / (aligned+undisclosed))")

    summary = {
        "n_skills": len(valid),
        "ic_distribution": dict(ic_counts),
        "avg_c_desc": round(sum(r.get("n_desc",0) for r in valid)/len(valid), 2),
        "avg_c_body": round(sum(r.get("n_body",0) for r in valid)/len(valid), 2),
        "avg_aligned": round(avg_aligned, 2),
        "avg_undisclosed": round(avg_undisclosed, 2),
        "avg_overclaimed": round(avg_overclaimed, 2),
        "description_coverage_pct": round(coverage_ratio * 100, 1),
    }
    REPORT.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n→ Results: {OUTFILE}")
    print(f"→ Report:  {REPORT}")


if __name__ == "__main__":
    main()
