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
Cap Alignment v3 — Parallel Full-Corpus Scanner
=================================================
Scans skills_sh full corpus (~14,886 analyzable skills) using
the v3 high-level domain matrix model, with thread-pool parallelism.

  python3 cap_alignment_parallel.py [--workers 4] [--n 0]
  --n 0 means ALL analyzable skills
"""

import json, re, sqlite3, subprocess, sys, time, threading
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse

DB      = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUTDIR  = Path(__file__).parent / "results/cap_alignment_full"
OUTFILE = OUTDIR / "results.jsonl"
REPORT  = OUTDIR / "summary.json"
OUTDIR.mkdir(parents=True, exist_ok=True)

WRITE_LOCK = threading.Lock()

# ── Section parser ─────────────────────────────────────────────────────────────

def extract_capability_sections(body: str, max_chars: int = 2000) -> str:
    if not body: return ""
    lines = body.split('\n')
    sections, current_header, current_lines = [], None, []
    for line in lines:
        if re.match(r'^#{1,4}\s', line):
            if current_lines and current_header:
                sections.append((current_header, '\n'.join(current_lines)))
            current_header, current_lines = line.strip(), []
        else:
            current_lines.append(line)
    if current_lines and current_header:
        sections.append((current_header, '\n'.join(current_lines)))

    USE_WHEN = re.compile(
        r'(use when|trigger when|when (to use|you need)|user (asks?|says?))',
        re.IGNORECASE)

    def score(header, content):
        h = header.lower()
        s = 0
        if any(k in h for k in ['when to use', 'trigger', 'features', 'capabilities']): s += 10
        if any(k in h for k in ['what', 'does', 'overview', 'functions', 'usage']): s += 5
        s += min(content.count('\n- ') + content.count('\n* '), 5)
        s += 3 if USE_WHEN.search(content) else 0
        return s

    scored = sorted(sections, key=lambda x: -score(x[0], x[1]))
    parts, total = [], 0
    for header, content in scored:
        chunk = f"{header}\n{content.strip()}\n"
        if total + len(chunk) <= max_chars:
            parts.append(chunk); total += len(chunk)
        elif total == 0:
            parts.append(chunk[:max_chars]); break
    return '\n'.join(parts) if parts else body[:max_chars]


# ── Prompt ─────────────────────────────────────────────────────────────────────

PROMPT = """\
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
  - MATCH        : capability is in BOTH body and description
  - IC1_HIDDEN   : capability is in body but NOT mentioned in description
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
  "notes": "optional 1-sentence summary"
}}"""


# ── LLM call (thread-safe, no shared state) ────────────────────────────────────

LLM_LOCK = threading.Semaphore(2)   # max 2 concurrent LLM calls

def llm(prompt: str, retries: int = 3) -> str | None:
    for attempt in range(retries):
        with LLM_LOCK:
            try:
                r = subprocess.run(
                    ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                    capture_output=True, text=True, timeout=150)
                text = json.loads(r.stdout).get("outputs", [{}])[0].get("text", "").strip()
                if text:
                    return text
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass
        time.sleep(2 + attempt * 3)  # backoff between retries
    return None

def parse_json(text: str) -> dict | None:
    if not text: return None
    for part in text.split("```"):
        part = part.strip().lstrip("json").strip()
        try: return json.loads(part)
        except: pass
    try: return json.loads(text)
    except: return None


# ── IC metrics ─────────────────────────────────────────────────────────────────

def compute_ic(matrix: dict, c_body: list, c_desc: list) -> dict:
    n_match     = len(matrix.get("match", []))
    n_hidden    = len(matrix.get("IC1_hidden", []))
    n_overclaim = len(matrix.get("IC4_overclaim", []))
    n_body      = max(len(c_body), n_match + n_hidden, 1)
    n_desc      = max(len(c_desc), n_match + n_overclaim, 1)
    cr  = n_match / n_body
    cp  = n_match / n_desc
    cdi = 1.0 - (cr + cp) / 2.0
    has_ic1 = n_hidden > 0
    has_ic4 = n_overclaim > 0
    if not has_ic1 and not has_ic4:  ic_type = "IC-0"
    elif has_ic1 and not has_ic4:    ic_type = "IC-1"
    elif not has_ic1 and has_ic4:    ic_type = "IC-4"
    else:                             ic_type = "IC-1+4"
    return {"n_body": n_body, "n_desc": n_desc, "n_match": n_match,
            "n_hidden": n_hidden, "n_overclaim": n_overclaim,
            "CR": round(cr, 4), "CP": round(cp, 4), "CDI": round(cdi, 4),
            "ic_type": ic_type}


# ── Process one skill ──────────────────────────────────────────────────────────

def process(slug: str, desc: str, body: str) -> dict:
    base = {"slug": slug, "desc_len": len(desc), "body_len": len(body)}
    excerpt = extract_capability_sections(body, max_chars=2000)
    prompt = PROMPT.replace("{desc}", desc[:800]).replace("{body_excerpt}", excerpt)
    raw = llm(prompt)
    r = parse_json(raw)
    if not r or "matrix" not in r:
        return {**base, "error": "parse_failed", "raw": (raw or "")[:200]}
    c_body = r.get("C_body", [])
    c_desc = r.get("C_desc", [])
    matrix = r.get("matrix", {})
    ic = compute_ic(matrix, c_body, c_desc)
    return {**base, "C_body": c_body, "C_desc": c_desc,
            "matrix": matrix, "notes": r.get("notes", ""),
            "body_excerpt_used": len(excerpt), **ic}


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--n",       type=int, default=0,  # 0 = all
                    help="Max skills to process (0=all)")
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    q = """
        SELECT skill_slug, description, skill_text
        FROM skills
        WHERE description IS NOT NULL
          AND skill_text  IS NOT NULL
          AND description != skill_text
          AND length(description)  BETWEEN 50 AND 1500
          AND length(skill_text)   > 500
          AND description NOT LIKE '%ERROR%'
          AND description NOT LIKE '%Insert instructions%'
        ORDER BY skill_slug
    """
    if args.n > 0: q += f" LIMIT {args.n}"
    rows = db.execute(q).fetchall()
    db.close()

    pool = {slug: (desc, body) for slug, desc, body in rows}
    print(f"Total analyzable: {len(pool):,} skills | workers={args.workers}")

    # Resume from previous run
    done = set()
    if OUTFILE.exists():
        for line in OUTFILE.read_text().strip().split('\n'):
            try: done.add(json.loads(line)["slug"])
            except: pass
    todo = [(slug, *pool[slug]) for slug in pool if slug not in done]
    print(f"Already done: {len(done):,} | Remaining: {len(todo):,}\n")

    counts = Counter()
    t0 = time.time()

    with open(OUTFILE, "a", buffering=1) as fout:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(process, slug, desc, body): slug
                       for slug, desc, body in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                slug = futures[fut]
                try:
                    result = fut.result()
                except Exception as e:
                    result = {"slug": slug, "error": str(e)}

                with WRITE_LOCK:
                    fout.write(json.dumps(result, ensure_ascii=False) + "\n")

                ic = result.get("ic_type", "ERR")
                counts[ic] += 1
                elapsed = time.time() - t0
                rate = i / elapsed * 60  # skills/min
                eta_min = (len(todo) - i) / max(rate / 60, 0.01) / 60

                if i % 10 == 0 or i <= 5:
                    print(f"[{i:5d}/{len(todo)}] {slug:40s} {ic:8s} "
                          f"| rate={rate:.1f}/min ETA={eta_min:.1f}h "
                          f"| IC-0={counts.get('IC-0',0)} "
                          f"IC-1={counts.get('IC-1',0)} "
                          f"IC-4={counts.get('IC-4',0)} "
                          f"IC-1+4={counts.get('IC-1+4',0)}")

    # ── Final summary ─────────────────────────────────────────────────────────
    all_results = [json.loads(l) for l in OUTFILE.read_text().strip().split('\n') if l]
    valid = [r for r in all_results if "error" not in r]

    print("\n" + "=" * 70)
    print(f"FINAL: {len(valid):,} valid / {len(all_results):,} total\n")
    ic_counts = Counter(r["ic_type"] for r in valid)
    for ic_t, n in sorted(ic_counts.items()):
        pct = n / max(len(valid), 1) * 100
        bar = "█" * int(pct / 2)
        print(f"  {ic_t:8s}  {n:5d}  {pct:5.1f}%  {bar}")

    avg = lambda k: sum(r.get(k, 0) for r in valid) / max(len(valid), 1)
    print(f"\nMatrix averages:")
    print(f"  C_body domains : {avg('n_body'):.2f}")
    print(f"  C_desc claims  : {avg('n_desc'):.2f}")
    print(f"  MATCH          : {avg('n_match'):.2f}")
    print(f"  IC-1 HIDDEN    : {avg('n_hidden'):.2f}  ← undisclosed per skill")
    print(f"  IC-4 OVERCLAIM : {avg('n_overclaim'):.2f}  ← overclaimed per skill")
    print(f"  CR (body cvg)  : {avg('CR'):.3f}")
    print(f"  CP (desc prec) : {avg('CP'):.3f}")
    print(f"  CDI (mismatch) : {avg('CDI'):.3f}")

    # Top IC-1 examples
    ic1 = sorted([r for r in valid if r["ic_type"] in ("IC-1","IC-1+4")],
                 key=lambda x: -x["n_hidden"])
    print(f"\nTop IC-1 (hidden caps) examples:")
    for r in ic1[:5]:
        h = r.get("matrix",{}).get("IC1_hidden",[])
        print(f"  {r['slug']:40s}  hidden={r['n_hidden']}  {h[:2]}")

    summary = {
        "platform": "skills_sh", "n_valid": len(valid), "n_total": len(all_results),
        "ic_distribution": dict(ic_counts),
        "ic1_rate": (ic_counts.get("IC-1",0) + ic_counts.get("IC-1+4",0)) / max(len(valid),1),
        "ic4_rate": (ic_counts.get("IC-4",0) + ic_counts.get("IC-1+4",0)) / max(len(valid),1),
        "avg_n_body":     round(avg("n_body"),  3),
        "avg_n_desc":     round(avg("n_desc"),  3),
        "avg_n_match":    round(avg("n_match"), 3),
        "avg_n_hidden":   round(avg("n_hidden"),    3),
        "avg_n_overclaim":round(avg("n_overclaim"), 3),
        "mean_CR":  round(avg("CR"),  3),
        "mean_CP":  round(avg("CP"),  3),
        "mean_CDI": round(avg("CDI"), 3),
    }
    REPORT.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n→ Results : {OUTFILE}")
    print(f"→ Report  : {REPORT}")

if __name__ == "__main__":
    main()
