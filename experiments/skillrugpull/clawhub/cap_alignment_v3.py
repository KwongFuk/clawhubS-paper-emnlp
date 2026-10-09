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
Cap Alignment v3 — High-Level Domain Alignment (Optimized)
===========================================================
Improvements over v2:
  - 1 LLM call per skill instead of 3  (3× faster)
  - Extracts HIGH-LEVEL capability domains (3-6), not fine-grained bullet phrases
  - Parses structured SKILL.md sections before LLM (no blind truncation)
  - LLM does direct yes/no coverage judgment (not phrase-matching matrix)
  - Evidence-backed: LLM cites where it found each domain in the body
  
Pipeline (per skill):
  1. Parse body → extract capability-dense sections (When to Use / Features / etc.)
  2. ONE LLM call:
       - Extract 3-6 high-level capability domains from body sections
       - For each domain: covered in description? (yes / partial / no)
       - For description: any claims not found in body? (IC-4 list)
  3. Programmatic IC metrics from structured output

Usage:
  python3 cap_alignment_v3.py [--n 200] [--slug some-skill-slug]
"""

import json, re, sqlite3, subprocess, sys, time
from pathlib import Path
from collections import Counter

DB     = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUTDIR = Path(__file__).parent / "results/cap_alignment_v3"
OUTFILE = OUTDIR / "results.jsonl"
REPORT  = OUTDIR / "summary.json"
OUTDIR.mkdir(parents=True, exist_ok=True)

# ── Section headers that indicate capability content ──────────────────────────

CAPABILITY_HEADERS = re.compile(
    r'^#{1,3}\s*(when to use|features?|capabilities?|what (it|this) does?|'
    r'commands?|trigger|usage|workflow|functions?|actions?|supported|'
    r'skills?|abilities|overview|purpose|description|behavior)',
    re.IGNORECASE | re.MULTILINE)

USE_WHEN = re.compile(
    r'(use when|trigger when|invoke when|call when|適用於|'
    r'use this (skill|when)|when (to use|you need|you want|user (asks?|says?|needs?)))',
    re.IGNORECASE)

def extract_capability_sections(body: str, max_chars: int = 2000) -> str:
    """
    Parse SKILL.md to extract capability-dense sections.
    Priority: When to Use > Features > Capabilities > other headers.
    Falls back to first max_chars if no structure found.
    """
    if not body: return ""
    lines = body.split('\n')
    
    sections = []
    current_header = None
    current_lines = []
    
    for line in lines:
        if re.match(r'^#{1,4}\s', line):
            if current_lines and current_header:
                sections.append((current_header, '\n'.join(current_lines)))
            current_header = line.strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines and current_header:
        sections.append((current_header, '\n'.join(current_lines)))
    
    # Score sections by capability relevance
    def section_score(header, content):
        h = header.lower()
        score = 0
        if any(k in h for k in ['when to use', 'trigger', 'use when']): score += 10
        if any(k in h for k in ['features', 'capabilities', 'functions']): score += 8
        if any(k in h for k in ['what', 'does', 'overview', 'purpose']): score += 5
        if any(k in h for k in ['workflow', 'commands', 'usage']): score += 4
        # Boost if content has bullet lists
        score += min(content.count('\n- ') + content.count('\n* '), 5)
        # Boost if content has "use when" phrases
        score += 3 if USE_WHEN.search(content) else 0
        return score
    
    scored = sorted(sections, key=lambda s: -section_score(s[0], s[1]))
    
    # Collect top sections up to max_chars
    result_parts = []
    total = 0
    for header, content in scored:
        chunk = f"{header}\n{content.strip()}\n"
        if total + len(chunk) <= max_chars:
            result_parts.append(chunk)
            total += len(chunk)
        elif total == 0:
            result_parts.append(chunk[:max_chars])
            break
    
    if result_parts:
        return '\n'.join(result_parts)
    
    # No structure found → use first max_chars
    return body[:max_chars]


# ── Combined extraction + alignment prompt (ONE call) ─────────────────────────

COMBINED_PROMPT = """\
You are auditing an AI skill for description-capability alignment.

DESCRIPTION (what the skill CLAIMS to do):
{desc}

SKILL BODY EXCERPT (what the skill ACTUALLY does):
{body_excerpt}

Task — build an alignment matrix:

Step 1. Extract 3-6 HIGH-LEVEL capability domains from the BODY.
        Use broad domain names (e.g. "task execution", "API integration").
        Each should be a distinct function actually demonstrated in the body.

Step 2. Extract 2-5 HIGH-LEVEL capability claims from the DESCRIPTION.
        Only include things explicitly stated (ignore vague marketing language).

Step 3. Classify each domain into exactly one cell:
  - MATCH       : claim is in BOTH body and description
  - IC1_HIDDEN  : capability is in body but NOT mentioned in description
  - IC4_OVERCLAIM: claim is in description but NOT supported by body

Return ONLY valid JSON:
{{
  "C_body": ["domain1", "domain2", ...],
  "C_desc": ["claim1", "claim2", ...],
  "matrix": {{
    "match":        ["capabilities present in both"],
    "IC1_hidden":   ["capabilities in body but NOT in description"],
    "IC4_overclaim":["claims in description but NOT supported by body"]
  }},
  "evidence": {{
    "<domain>": "brief quote from body supporting it"
  }},
  "notes": "optional 1-sentence summary"
}}"""


# ── LLM helpers ───────────────────────────────────────────────────────────────

def llm(prompt: str, retries: int = 3) -> str | None:
    for attempt in range(retries):
        try:
            r = subprocess.run(
                ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120)
            return json.loads(r.stdout).get("outputs", [{}])[0].get("text", "").strip()
        except subprocess.TimeoutExpired:
            print(f"    [TIMEOUT] attempt {attempt+1}/{retries}")
            time.sleep(5)
        except Exception as e:
            print(f"    [ERR] {e}")
    return None

def parse_json(text: str) -> dict | None:
    if not text: return None
    for part in text.split("```"):
        part = part.strip().lstrip("json").strip()
        try: return json.loads(part)
        except: pass
    try: return json.loads(text)
    except: return None


# ── IC computation from explicit matrix ──────────────────────────────────────

def compute_ic(matrix: dict, c_body: list, c_desc: list) -> dict:
    """
    Matrix cells:
      match        : in BOTH body and description  (aligned)
      IC1_hidden   : in body, NOT in description   (undisclosed)
      IC4_overclaim: in description, NOT in body   (overclaimed)
    """
    n_match     = len(matrix.get("match", []))
    n_hidden    = len(matrix.get("IC1_hidden", []))
    n_overclaim = len(matrix.get("IC4_overclaim", []))
    n_body      = max(len(c_body), n_match + n_hidden, 1)
    n_desc      = max(len(c_desc), n_match + n_overclaim, 1)

    # Capability Recall  = how much of body is mentioned in desc
    cr  = n_match / n_body
    # Capability Precision = how much of desc is actually in body
    cp  = n_match / n_desc
    # Composite Disclosure Index (higher = worse)
    cdi = 1.0 - (cr + cp) / 2.0

    has_ic1 = n_hidden    > 0
    has_ic4 = n_overclaim > 0

    if not has_ic1 and not has_ic4:  ic_type = "IC-0"
    elif has_ic1 and not has_ic4:    ic_type = "IC-1"
    elif not has_ic1 and has_ic4:    ic_type = "IC-4"
    else:                             ic_type = "IC-1+4"

    return {
        "n_body":      n_body,
        "n_desc":      n_desc,
        "n_match":     n_match,
        "n_hidden":    n_hidden,
        "n_overclaim": n_overclaim,
        "CR":  round(cr,  4),
        "CP":  round(cp,  4),
        "CDI": round(cdi, 4),
        "ic_type": ic_type,
    }


# ── Process one skill ─────────────────────────────────────────────────────────

def process_skill(slug: str, description: str, body: str) -> dict:
    base = {"slug": slug, "desc_len": len(description), "body_len": len(body)}

    # Parse body for capability sections
    excerpt = extract_capability_sections(body, max_chars=2000)

    # ONE combined LLM call → dual-set extraction + matrix
    prompt = COMBINED_PROMPT.format(desc=description[:800], body_excerpt=excerpt)
    raw = llm(prompt)
    result = parse_json(raw)

    if not result or "matrix" not in result:
        return {**base, "error": "parse_failed", "raw": (raw or "")[:300]}

    c_body    = result.get("C_body", [])
    c_desc    = result.get("C_desc", [])
    matrix    = result.get("matrix", {})
    evidence  = result.get("evidence", {})
    notes     = result.get("notes", "")

    ic = compute_ic(matrix, c_body, c_desc)

    return {
        **base,
        "C_body":   c_body,
        "C_desc":   c_desc,
        "matrix":   matrix,
        "evidence": evidence,
        "notes":    notes,
        "body_excerpt_used": len(excerpt),
        **ic,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n",    type=int, default=200,
                    help="Number of skills to process (default 200)")
    ap.add_argument("--slug", type=str, default=None,
                    help="Process a single skill by slug (for testing)")
    args = ap.parse_args()

    db = sqlite3.connect(DB)

    if args.slug:
        # Single-skill mode (debugging / inspection)
        row = db.execute(
            "SELECT skill_slug, description, skill_text FROM skills WHERE skill_slug=?",
            (args.slug,)).fetchone()
        db.close()
        if not row:
            print(f"Slug not found: {args.slug}"); return
        slug, desc, body = row
        result = process_skill(slug, desc or "", body or "")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Batch mode
    rows = db.execute("""
        SELECT skill_slug, description, skill_text
        FROM skills
        WHERE description IS NOT NULL
          AND skill_text  IS NOT NULL
          AND description != skill_text
          AND length(description)  BETWEEN 50 AND 1500
          AND length(skill_text)   > 500
          AND description NOT LIKE '%ERROR%'
          AND description NOT LIKE '%Insert instructions%'
        ORDER BY RANDOM()
        LIMIT ?
    """, (args.n * 3,)).fetchall()
    db.close()

    seen, pool = set(), []
    for slug, desc, body in rows:
        if slug not in seen:
            seen.add(slug)
            pool.append((slug, desc, body))
        if len(pool) == args.n: break

    print(f"Pool: {len(pool)} skills  (target n={args.n})\n")

    # Resume
    done, results = set(), []
    if OUTFILE.exists():
        for line in OUTFILE.read_text().strip().split('\n'):
            try:
                r = json.loads(line)
                done.add(r["slug"])
                results.append(r)
            except: pass

    todo = [(s, d, b) for s, d, b in pool if s not in done]
    print(f"Already done: {len(done)} | Remaining: {len(todo)}\n")

    with open(OUTFILE, "a") as f:
        for i, (slug, desc, body) in enumerate(todo):
            print(f"[{i+1:4d}/{len(todo)}] {slug}")
            result = process_skill(slug, desc, body)
            results.append(result)
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()

            if "error" in result:
                print(f"  [ERROR] {result['error']}")
            else:
                ic = result["ic_type"]
                mx = result.get("matrix", {})
                print(f"  {ic:8s}  CR={result['CR']:.2f} CP={result['CP']:.2f} "
                      f"CDI={result['CDI']:.2f}  "
                      f"match={result['n_match']} hidden={result['n_hidden']} "
                      f"overclaim={result['n_overclaim']}")
                if mx.get("IC1_hidden"):
                    print(f"  IC-1 hidden:    {mx['IC1_hidden'][:3]}")
                if mx.get("IC4_overclaim"):
                    print(f"  IC-4 overclaim: {mx['IC4_overclaim'][:2]}")
            print()
            time.sleep(0.2)

    # ── Summary ───────────────────────────────────────────────────────────────
    valid = [r for r in results if "error" not in r]
    print("=" * 65)
    print(f"RESULTS: {len(valid)} valid / {len(results)} total\n")

    ic_counts = Counter(r["ic_type"] for r in valid)
    print("IC Type Distribution:")
    for ic_t, n in sorted(ic_counts.items()):
        print(f"  {ic_t:8s}  {n:4d}  ({n/max(len(valid),1)*100:.1f}%)")

    avg = lambda key: sum(r.get(key, 0) for r in valid) / max(len(valid), 1)
    print(f"\nAverage per skill (matrix cells):")
    print(f"  C_body domains:  {avg('n_body'):.1f}")
    print(f"  C_desc claims:   {avg('n_desc'):.1f}")
    print(f"  MATCH:           {avg('n_match'):.1f}")
    print(f"  IC-1 HIDDEN:     {avg('n_hidden'):.1f}  (body has, desc omits)")
    print(f"  IC-4 OVERCLAIM:  {avg('n_overclaim'):.1f}  (desc claims, body lacks)")
    print(f"  CR:              {avg('CR'):.3f}  (match/body)")
    print(f"  CP:              {avg('CP'):.3f}  (match/desc)")
    print(f"  CDI:             {avg('CDI'):.3f}  (mismatch index)")

    # Top IC-1 examples
    ic1 = sorted([r for r in valid if r["ic_type"] in ("IC-1","IC-1+4")],
                 key=lambda x: -x["n_hidden"])
    print(f"\nTop IC-1 examples ({len(ic1)} skills with hidden caps):")
    for r in ic1[:5]:
        hidden = r.get("matrix", {}).get("IC1_hidden", [])
        print(f"  {r['slug']:35s}  hidden={r['n_hidden']}  {hidden[:2]}")

    # Top IC-4 examples
    ic4 = sorted([r for r in valid if r["ic_type"] in ("IC-4","IC-1+4")],
                 key=lambda x: -x["n_overclaim"])
    print(f"\nTop IC-4 examples ({len(ic4)} skills with overclaims):")
    for r in ic4[:5]:
        overclaim = r.get("matrix", {}).get("IC4_overclaim", [])
        print(f"  {r['slug']:35s}  overclaim={r['n_overclaim']}  {overclaim[:2]}")

    summary = {
        "n_valid": len(valid), "n_total": len(results),
        "ic_distribution": dict(ic_counts),
        "avg_n_body":      round(avg("n_body"),  2),
        "avg_n_desc":      round(avg("n_desc"),  2),
        "avg_n_match":     round(avg("n_match"), 2),
        "avg_n_hidden":    round(avg("n_hidden"),    2),
        "avg_n_overclaim": round(avg("n_overclaim"), 2),
        "mean_CR":  round(avg("CR"),  3),
        "mean_CP":  round(avg("CP"),  3),
        "mean_CDI": round(avg("CDI"), 3),
        "ic1_rate": (ic_counts.get("IC-1",0) + ic_counts.get("IC-1+4",0))
                    / max(len(valid), 1),
        "ic4_rate": (ic_counts.get("IC-4",0) + ic_counts.get("IC-1+4",0))
                    / max(len(valid), 1),
    }
    REPORT.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n→ Results: {OUTFILE}")
    print(f"→ Report:  {REPORT}")


if __name__ == "__main__":
    main()
