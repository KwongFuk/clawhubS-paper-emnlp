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
Capability Alignment v2 — Structured Extraction + Programmatic IC Computation
==============================================================================

Pipeline (4 steps):

  Step 1: LLM extracts C_desc
          Input: description text
          Output: {"capabilities": ["cap1", "cap2", ...]}  ← structured list

  Step 2: LLM extracts C_body
          Input: skill body (first 2500 chars)
          Output: {"capabilities": ["cap3", "cap4", ...]}  ← structured list

  Step 3: LLM produces matching matrix
          Input: C_desc list + C_body list
          Output: for each C_body cap → which C_desc cap covers it (or null)
                  for each C_desc cap → which C_body caps it covers (or [])
          ← LLM only does semantic matching, NOT IC labeling

  Step 4: PROGRAMMATIC IC metrics (same formulas as old system)
          C_aligned     = body caps with a desc match
          C_undisclosed = body caps with NO desc match  → IC-1
          C_overclaimed = desc caps covering NO body cap → IC-4
          CR = |C_aligned| / |C_body|     (Capability Recall)
          CP = 1 - |C_overclaimed| / |C_desc|  (Capability Precision)
          IC-type classification (same threshold as old system)

Tests on 100 non-official ClawHub skills.
"""

import json, sqlite3, subprocess, time
from pathlib import Path
from collections import Counter

DB      = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUTDIR  = Path(__file__).parent / "results" / "cap_alignment_v2"
OUTFILE = OUTDIR / "cap_alignment_v2_100.jsonl"
REPORT  = OUTDIR / "summary_v2.json"

# ── Prompts ──────────────────────────────────────────────────────────────────

EXTRACT_DESC = """\
Extract the high-level functional capabilities explicitly stated in this skill description.
List ONLY what is clearly stated. Use short noun phrases (2-6 words each).
Return JSON only: {{"capabilities": ["cap1", "cap2", ...]}}

Description:
{text}"""

EXTRACT_BODY = """\
Extract all distinct high-level functional capabilities supported by this skill, \
based on its documentation.
Use short noun phrases (2-6 words each). Include every distinct function you find.
Return JSON only: {{"capabilities": ["cap1", "cap2", ...]}}

Skill documentation (excerpt):
{text}"""

MATCH_PROMPT = """\
You are given two capability lists for the same AI skill:

C_desc (declared in description, {n_desc} items):
{c_desc_json}

C_body (found in actual documentation, {n_body} items):
{c_body_json}

Task: For each capability, determine semantic coverage.

Return JSON with exactly this structure:
{{
  "body_to_desc": {{
    "<body_cap>": "<matching_desc_cap_or_null>",
    ...
  }},
  "desc_to_body": {{
    "<desc_cap>": ["<matching_body_cap>", ...],
    ...
  }}
}}

Rules:
- body_to_desc: for EACH C_body item, provide the best matching C_desc item (exact string from C_desc list), or null if no semantic match
- desc_to_body: for EACH C_desc item, provide ALL C_body items it semantically covers (exact strings from C_body list), or [] if it covers nothing
- "semantic match" means the same real-world function, even if worded differently
- Return ONLY the JSON, no explanation"""

# ── LLM call ─────────────────────────────────────────────────────────────────

def llm(prompt: str, retries: int = 3) -> str | None:
    for attempt in range(retries):
        try:
            r = subprocess.run(
                ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120,
            )
            j = json.loads(r.stdout)
            return j.get("outputs", [{}])[0].get("text", "").strip()
        except subprocess.TimeoutExpired:
            print(f"    [WARN] timeout attempt {attempt+1}/{retries}")
            time.sleep(5)
        except Exception as e:
            print(f"    [ERR] {e}")
    return None


def parse_json(text: str) -> dict | None:
    if not text:
        return None
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
        return None


# ── IC computation (programmatic, same formula as old system) ─────────────────

def compute_ic(c_desc: list, c_body: list,
               body_to_desc: dict, desc_to_body: dict) -> dict:
    """
    Programmatically compute IC metrics from the matching matrix.
    Same formula as old system (CR/CP-based).
    """
    # C_aligned: body caps that have a desc match
    c_aligned     = [c for c in c_body if body_to_desc.get(c) is not None]
    # C_undisclosed: body caps with NO desc match → IC-1
    c_undisclosed = [c for c in c_body if body_to_desc.get(c) is None]
    # C_overclaimed: desc caps that cover NO body cap → IC-4
    c_overclaimed = [c for c in c_desc
                     if not desc_to_body.get(c)]  # empty list or missing

    n_body = max(len(c_body), 1)
    n_desc = max(len(c_desc), 1)

    cr  = len(c_aligned) / n_body          # Capability Recall
    cp  = 1.0 - len(c_overclaimed) / n_desc  # Capability Precision
    cdi = 1.0 - (cr + cp) / 2.0           # Capability Disclosure Index

    # IC classification (same threshold as old system)
    has_ic1 = cr < 1.0  # any undisclosed capabilities
    has_ic4 = cp < 1.0  # any overclaimed capabilities

    if not has_ic1 and not has_ic4:
        ic_type = "IC-0"
    elif has_ic1 and not has_ic4:
        ic_type = "IC-1"
    elif not has_ic1 and has_ic4:
        ic_type = "IC-4"
    else:
        ic_type = "IC-1+4"

    return {
        "c_aligned":     c_aligned,
        "c_undisclosed": c_undisclosed,
        "c_overclaimed": c_overclaimed,
        "n_aligned":     len(c_aligned),
        "n_undisclosed": len(c_undisclosed),
        "n_overclaimed": len(c_overclaimed),
        "CR":  round(cr,  4),
        "CP":  round(cp,  4),
        "CDI": round(cdi, 4),
        "ic_type": ic_type,
    }


# ── Process one skill ─────────────────────────────────────────────────────────

def process_skill(slug: str, description: str, body: str) -> dict:
    base = {"slug": slug, "desc_len": len(description), "body_len": len(body)}

    # Step 1: extract C_desc
    r1 = parse_json(llm(EXTRACT_DESC.format(text=description)))
    if not r1:
        return {**base, "error": "desc_extract_failed"}
    c_desc = r1.get("capabilities", [])

    # Step 2: extract C_body
    r2 = parse_json(llm(EXTRACT_BODY.format(text=body[:2500])))
    if not r2:
        return {**base, "error": "body_extract_failed"}
    c_body = r2.get("capabilities", [])

    if not c_desc or not c_body:
        return {**base, "error": "empty_extraction",
                "c_desc": c_desc, "c_body": c_body}

    # Step 3: matching matrix (LLM gives STRUCTURE, not IC labels)
    r3 = parse_json(llm(MATCH_PROMPT.format(
        n_desc=len(c_desc),
        n_body=len(c_body),
        c_desc_json=json.dumps(c_desc, ensure_ascii=False),
        c_body_json=json.dumps(c_body, ensure_ascii=False),
    )))
    if not r3:
        return {**base, "error": "match_failed",
                "c_desc": c_desc, "c_body": c_body}

    body_to_desc = r3.get("body_to_desc", {})
    desc_to_body = r3.get("desc_to_body", {})

    # Step 4: programmatic IC computation
    ic = compute_ic(c_desc, c_body, body_to_desc, desc_to_body)

    return {
        **base,
        "c_desc":      c_desc,
        "c_body":      c_body,
        "n_desc":      len(c_desc),
        "n_body":      len(c_body),
        "body_to_desc": body_to_desc,
        "desc_to_body": desc_to_body,
        **ic,           # CR, CP, CDI, ic_type, c_aligned, c_undisclosed, c_overclaimed
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(DB)
    cur = db.cursor()
    cur.execute("""
      SELECT skill_slug, description, skill_text
      FROM skills
      WHERE description IS NOT NULL
        AND skill_text IS NOT NULL
        AND description != skill_text
        AND length(description) BETWEEN 80 AND 1200
        AND length(skill_text) > 800
        AND description NOT LIKE '%ERROR%'
        AND description NOT LIKE '%Insert instructions%'
      ORDER BY RANDOM()
      LIMIT 300
    """)
    rows = cur.fetchall()
    db.close()

    seen, pool = set(), []
    for slug, desc, body in rows:
        if slug not in seen:
            seen.add(slug)
            pool.append((slug, desc, body))
        if len(pool) == 100:
            break

    print(f"Pool: {len(pool)} non-official skills\n")

    # Resume
    done, results = set(), []
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
            print(f"[{i+1}/{len(todo)}] {slug}")

            result = process_skill(slug, desc, body)
            results.append(result)
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()

            if "error" in result:
                print(f"  [ERROR] {result['error']}")
            else:
                print(f"  IC={result['ic_type']:8s}  "
                      f"CR={result['CR']:.2f}  CP={result['CP']:.2f}  CDI={result['CDI']:.2f}  "
                      f"undisclosed={result['n_undisclosed']}  overclaimed={result['n_overclaimed']}  "
                      f"aligned={result['n_aligned']}")
                if result.get("c_undisclosed"):
                    print(f"  Undisclosed: {result['c_undisclosed'][:3]}")
                if result.get("c_overclaimed"):
                    print(f"  Overclaimed: {result['c_overclaimed'][:3]}")
            print()
            time.sleep(0.3)

    # ── Summary ───────────────────────────────────────────────────────────────
    valid = [r for r in results if "error" not in r]
    print("=" * 65)
    print(f"RESULTS: {len(valid)} / {len(results)} skills (valid)\n")

    ic_counts = Counter(r["ic_type"] for r in valid)
    print("IC Type Distribution:")
    for ic, n in sorted(ic_counts.items()):
        print(f"  {ic:8s}  {n:4d}  ({n/len(valid)*100:.1f}%)")

    avg = lambda key: sum(r.get(key, 0) for r in valid) / max(len(valid), 1)
    print(f"\nAverage metrics:")
    print(f"  n_desc:       {avg('n_desc'):.1f}  capabilities extracted from description")
    print(f"  n_body:       {avg('n_body'):.1f}  capabilities extracted from body")
    print(f"  n_aligned:    {avg('n_aligned'):.1f}")
    print(f"  n_undisclosed:{avg('n_undisclosed'):.1f}  (IC-1 gap per skill)")
    print(f"  n_overclaimed:{avg('n_overclaimed'):.1f}  (IC-4 gap per skill)")
    print(f"  CR:           {avg('CR'):.3f}  (mean Capability Recall)")
    print(f"  CP:           {avg('CP'):.3f}  (mean Capability Precision)")
    print(f"  CDI:          {avg('CDI'):.3f}  (mean mismatch index)")

    summary = {
        "n_valid": len(valid), "n_total": len(results),
        "ic_distribution": dict(ic_counts),
        "avg_n_desc":       round(avg("n_desc"), 2),
        "avg_n_body":       round(avg("n_body"), 2),
        "avg_n_aligned":    round(avg("n_aligned"), 2),
        "avg_n_undisclosed":round(avg("n_undisclosed"), 2),
        "avg_n_overclaimed":round(avg("n_overclaimed"), 2),
        "mean_CR":  round(avg("CR"),  3),
        "mean_CP":  round(avg("CP"),  3),
        "mean_CDI": round(avg("CDI"), 3),
    }
    REPORT.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n→ Results: {OUTFILE}")
    print(f"→ Report:  {REPORT}")


if __name__ == "__main__":
    main()
